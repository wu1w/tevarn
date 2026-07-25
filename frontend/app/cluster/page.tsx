'use client';

import ClusterPanel from '@/components/cluster/ClusterPanel';

export default function ClusterPage() {
  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl">
        <ClusterPanel />
      </div>
    </div>
  );
}
