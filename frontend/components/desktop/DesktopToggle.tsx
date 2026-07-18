'use client';

import { useState } from 'react';
import { Monitor, MonitorOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { DesktopAgentPanel } from './DesktopAgentPanel';

interface DesktopToggleProps {
  onTaskComplete?: (result: any) => void;
}

export function DesktopToggle({ onTaskComplete }: DesktopToggleProps) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant={isOpen ? "default" : "ghost"}
              size="icon"
              className="h-9 w-9"
              onClick={() => setIsOpen(!isOpen)}
            >
              {isOpen ? (
                <Monitor className="h-4 w-4" />
              ) : (
                <MonitorOff className="h-4 w-4" />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>{isOpen ? "关闭桌面助手" : "打开桌面助手"}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <DesktopAgentPanel 
        isOpen={isOpen} 
        onClose={() => setIsOpen(false)}
        onTaskComplete={onTaskComplete}
      />
    </>
  );
}
