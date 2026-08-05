import 'dart:io';

/// Enumerate non-loopback IPv4 addresses for network fingerprinting.
Future<List<String>> listIpv4() async {
  try {
    final ifs = await NetworkInterface.list(
      includeLinkLocal: false,
      type: InternetAddressType.IPv4,
    );
    final out = <String>[];
    for (final ni in ifs) {
      for (final a in ni.addresses) {
        final ip = a.address;
        if (ip.startsWith('127.')) continue;
        if (!out.contains(ip)) out.add(ip);
      }
    }
    out.sort();
    return out;
  } catch (_) {
    return const [];
  }
}
