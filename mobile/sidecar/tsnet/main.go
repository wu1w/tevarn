// Takton tsnet embed (userspace Tailscale).
//
// Roles:
//   pc    — join tailnet + reverse-proxy local backend onto the mesh
//   phone — join tailnet only (dial-out client); no public services
//
// Both are spawned by the Takton host engine so users never run tailscale CLI.
//
// Build:  cd sidecar/tsnet && go mod tidy && go build -o takton-tsnet .
//
// PC:    TAKTON_TS_AUTHKEY=… ./takton-tsnet -role pc -backend 127.0.0.1:8090
// Phone: TAKTON_TS_AUTHKEY=… ./takton-tsnet -role phone -client-only

package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"tailscale.com/tsnet"
)

func main() {
	backend := flag.String("backend", envOr("TAKTON_BACKEND", "127.0.0.1:8090"), "local backend host:port (pc role)")
	listen := flag.String("listen", envOr("TAKTON_TSNET_LISTEN", ":8090"), "tailnet listen (pc role)")
	hostname := flag.String("hostname", envOr("TAKTON_TSNET_HOSTNAME", "takton-node"), "tailnet hostname")
	dir := flag.String("state", envOr("TAKTON_TSNET_DIR", ""), "tsnet state directory")
	health := flag.String("health", envOr("TAKTON_TSNET_HEALTH", ""), "health file path when up")
	statusAddr := flag.String("status", envOr("TAKTON_TSNET_STATUS", "127.0.0.1:17891"), "local status HTTP")
	role := flag.String("role", envOr("TAKTON_TSNET_ROLE", "pc"), "pc | phone")
	clientOnly := flag.Bool("client-only", false, "join mesh only, no reverse proxy")
	ephemeral := flag.Bool("ephemeral", false, "ephemeral node (recommended for phone)")
	flag.Parse()

	// Phone defaults to ephemeral client-only
	if *role == "phone" || *role == "client" || *role == "mobile" {
		*clientOnly = true
		if !*ephemeral {
			// default ephemeral for phone unless state dir already exists
			*ephemeral = true
		}
		if *hostname == "takton-node" || *hostname == "takton-pc" {
			*hostname = envOr("TAKTON_TSNET_HOSTNAME", "takton-phone")
		}
	}

	authKey := os.Getenv("TS_AUTHKEY")
	if authKey == "" {
		authKey = os.Getenv("TAKTON_TS_AUTHKEY")
	}
	if authKey == "" {
		log.Fatal("TS_AUTHKEY or TAKTON_TS_AUTHKEY required")
	}

	stateDir := *dir
	if stateDir == "" {
		home, _ := os.UserHomeDir()
		stateDir = filepath.Join(home, ".takton", "tsnet-"+*role)
	}
	_ = os.MkdirAll(stateDir, 0o700)

	srv := &tsnet.Server{
		Hostname:  *hostname,
		AuthKey:   authKey,
		Dir:       stateDir,
		Ephemeral: *ephemeral,
	}
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	st, err := srv.Up(ctx)
	if err != nil {
		log.Fatalf("tsnet up: %v", err)
	}
	ips := st.TailscaleIPs
	log.Printf("tsnet online role=%s hostname=%s ips=%v ephemeral=%v", *role, *hostname, ips, *ephemeral)

	if *health != "" {
		_ = os.WriteFile(*health, []byte(fmt.Sprintf("ok %s %v\n", *role, time.Now())), 0o600)
	}

	// Always expose local status for the Takton host to probe (no auth — loopback only)
	go localStatus(*statusAddr, *role, *hostname, ips)

	if !*clientOnly {
		ln, err := srv.Listen("tcp", *listen)
		if err != nil {
			log.Fatalf("listen: %v", err)
		}
		log.Printf("proxy %s → %s", *listen, *backend)
		target, err := url.Parse("http://" + *backend)
		if err != nil {
			log.Fatal(err)
		}
		proxy := httputil.NewSingleHostReverseProxy(target)
		orig := proxy.Director
		proxy.Director = func(r *http.Request) {
			orig(r)
			r.Host = target.Host
			r.Header.Set("X-Takton-Mesh", "tsnet")
			r.Header.Set("X-Takton-Role", *role)
		}
		proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, e error) {
			log.Printf("proxy error: %v", e)
			http.Error(w, "backend unavailable", http.StatusBadGateway)
		}
		httpSrv := &http.Server{Handler: proxy}
		go func() {
			if err := httpSrv.Serve(ln); err != nil && err != http.ErrServerClosed {
				log.Fatalf("serve: %v", err)
			}
		}()
		defer httpSrv.Close()
	} else {
		log.Printf("client-only mode · no reverse proxy")
	}

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	log.Printf("shutting down")
	if *health != "" {
		_ = os.Remove(*health)
	}
}

func localStatus(addr, role, hostname string, ips []net.IP) {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "ok\n")
	})
	mux.HandleFunc("/ip", func(w http.ResponseWriter, r *http.Request) {
		for _, ip := range ips {
			if ip.To4() != nil {
				io.WriteString(w, ip.String())
				return
			}
		}
		if len(ips) > 0 {
			io.WriteString(w, ips[0].String())
		}
	})
	mux.HandleFunc("/status", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		var ip4 string
		for _, ip := range ips {
			if ip.To4() != nil {
				ip4 = ip.String()
				break
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"ok":       true,
			"role":     role,
			"hostname": hostname,
			"ip":       ip4,
			"ips":      ipsToStrings(ips),
		})
	})
	// Prefer loopback-only bind for status
	if addr == "" {
		addr = "127.0.0.1:17891"
	}
	log.Printf("local status on %s", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Printf("status server: %v", err)
	}
}

func ipsToStrings(ips []net.IP) []string {
	out := make([]string, 0, len(ips))
	for _, ip := range ips {
		out = append(out, ip.String())
	}
	return out
}

func envOr(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
