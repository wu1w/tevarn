'use client';

import React, { useState, useCallback } from 'react';
import { FileTreeItem } from '@/types';

interface FileTreeProps {
  items: FileTreeItem[];
  onSelectFile?: (path: string) => void;
  selectedPath?: string;
  level?: number;
}

export function FileTree({ items, onSelectFile, selectedPath, level = 0 }: FileTreeProps) {
  return (
    <div className="select-none">
      {items.length === 0 && level === 0 && (
        <div className="px-3 py-4 text-center text-[11px] text-foreground-dim">
          无文件
        </div>
      )}
      {items.map((item) => (
        <FileTreeNode
          key={item.path}
          item={item}
          onSelectFile={onSelectFile}
          selectedPath={selectedPath}
          level={level}
        />
      ))}
    </div>
  );
}

function FileTreeNode({
  item,
  onSelectFile,
  selectedPath,
  level,
}: {
  item: FileTreeItem;
  onSelectFile?: (path: string) => void;
  selectedPath?: string;
  level: number;
}) {
  const [expanded, setExpanded] = useState(level < 2);
  const isDir = item.type === 'directory';
  const isSelected = selectedPath === item.path;

  const handleClick = useCallback(() => {
    if (isDir) {
      setExpanded((v) => !v);
    } else {
      onSelectFile?.(item.path);
    }
  }, [isDir, item.path, onSelectFile]);

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        onClick={handleClick}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            handleClick();
          }
        }}
        className={`flex items-center gap-1.5 rounded px-2 py-1 text-[12px] cursor-pointer transition-colors ${
          isSelected
            ? 'bg-brand-purple/15 text-brand-cyan font-medium'
            : 'text-foreground-dim hover:bg-card-bg-hover hover:text-foreground'
        }`}
        style={{ paddingLeft: `${8 + level * 14}px` }}
        title={item.path}
      >
        {/* Icon */}
        {isDir ? (
          <svg
            className={`h-3.5 w-3.5 flex-shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d={expanded ? "M19 9l-7 7-7-7" : "M9 5l7 7-7 7"}
            />
          </svg>
        ) : (
          <svg className="h-3.5 w-3.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"
            />
          </svg>
        )}

        <span className="flex-1 truncate">{item.name}</span>

        {/* File size */}
        {!isDir && item.size !== undefined && (
          <span className="text-[9px] text-foreground-dim flex-shrink-0">
            {item.size > 1024 ? `${(item.size / 1024).toFixed(1)}k` : `${item.size}B`}
          </span>
        )}
      </div>

      {/* Children */}
      {isDir && expanded && item.children && (
        <FileTree
          items={item.children}
          onSelectFile={onSelectFile}
          selectedPath={selectedPath}
          level={level + 1}
        />
      )}
    </div>
  );
}
