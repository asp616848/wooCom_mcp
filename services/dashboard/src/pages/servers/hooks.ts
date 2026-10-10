import { useQuery } from '@tanstack/react-query';

import { serversApi } from '@/lib/api';

/** Servers poll every 5 s so status changes (health checks) show up without a reload (DESIGN §11). */
export function useServers() {
  return useQuery({ queryKey: ['servers'], queryFn: serversApi.list, refetchInterval: 5000 });
}
