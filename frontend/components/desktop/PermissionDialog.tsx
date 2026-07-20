'use client';

import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Monitor, Shield, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { useT } from '@/stores/localeStore';

export type PermissionLevel = 'ask' | 'allow_once' | 'allow_session' | 'always_allow';

export interface DesktopPermissionRequest {
  operation: string;
  operationLabel: string;
  appName?: string;
  description: string;
}

interface DesktopPermissionDialogProps {
  open: boolean;
  request: DesktopPermissionRequest | null;
  onAllow: (level: PermissionLevel, rememberApp: boolean) => void;
  onDeny: () => void;
}

export function DesktopPermissionDialog({
  open,
  request,
  onAllow,
  onDeny,
}: DesktopPermissionDialogProps) {
  const t = useT();
  const [rememberApp, setRememberApp] = useState(false);

  if (!request) return null;

  const handleAllow = (level: PermissionLevel) => {
    onAllow(level, rememberApp);
    setRememberApp(false);
  };

  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            transition={{ duration: 0.15 }}
          >
            <Card className="w-[420px] border-2 shadow-xl">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10">
                      <Monitor className="h-5 w-5 text-primary" />
                    </div>
                    <div>
                      <CardTitle className="text-lg">{t('desktop._e67')}</CardTitle>
                      <CardDescription className="text-sm">
                        Takton 想要控制您的电脑
                      </CardDescription>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    onClick={onDeny}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              </CardHeader>

              <CardContent className="space-y-4">
                {/* 操作详情 */}
                <div className="rounded-lg bg-muted p-3">
                  <div className="flex items-start gap-2">
                    <Shield className="mt-0.5 h-4 w-4 text-muted-foreground" />
                    <div className="flex-1 space-y-1">
                      <p className="text-sm font-medium">{request.operationLabel}</p>
                      <p className="text-xs text-muted-foreground">
                        {request.description}
                      </p>
                      {request.appName && (
                        <p className="text-xs text-muted-foreground">
                          目标应用: <span className="font-mono">{request.appName}</span>
                        </p>
                      )}
                    </div>
                  </div>
                </div>

                {/* 权限选项 */}
                <div className="space-y-2">
                  <Button
                    variant="outline"
                    className="w-full justify-start"
                    onClick={() => handleAllow('allow_once')}
                  >
                    <span className="flex-1 text-left">{t('desktop._e68')}</span>
                    <span className="text-xs text-muted-foreground">{t('desktop._e69')}</span>
                  </Button>

                  <Button
                    variant="outline"
                    className="w-full justify-start"
                    onClick={() => handleAllow('allow_session')}
                  >
                    <span className="flex-1 text-left">{t('desktop._e70')}</span>
                    <span className="text-xs text-muted-foreground">{t('desktop._e71')}</span>
                  </Button>

                  <Button
                    variant="default"
                    className="w-full justify-start"
                    onClick={() => handleAllow('always_allow')}
                  >
                    <span className="flex-1 text-left">{t('desktop._e72')}</span>
                    <span className="text-xs opacity-70">{t('desktop._e73')}</span>
                  </Button>
                </div>

                {/* 记住应用选项 */}
                {request.appName && (
                  <div className="flex items-center space-x-2 pt-2">
                    <Checkbox
                      id="remember-app"
                      checked={rememberApp}
                      onCheckedChange={(checked) => setRememberApp(checked as boolean)}
                    />
                    <label
                      htmlFor="remember-app"
                      className="text-sm text-muted-foreground cursor-pointer"
                    >
                      记住此应用（{request.appName}）的选择
                    </label>
                  </div>
                )}

                {/* 拒绝按钮 */}
                <Button
                  variant="ghost"
                  className="w-full text-destructive hover:text-destructive"
                  onClick={onDeny}
                >
                  拒绝
                </Button>

                {/* 安全提示 */}
                <div className="flex items-start gap-2 rounded-lg bg-amber-500/10 p-2 text-xs text-amber-600 dark:text-amber-400">
                  <AlertTriangle className="mt-0.5 h-3 w-3 flex-shrink-0" />
                  <p>
                    允许桌面控制后，Takton 可以模拟键盘鼠标操作。
                    请确保您信任当前任务。
                  </p>
                </div>
              </CardContent>
            </Card>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
