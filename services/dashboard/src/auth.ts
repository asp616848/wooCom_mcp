import { useQuery } from '@tanstack/react-query';

import { ApiError, authApi } from '@/lib/api';

/** The signed-in user, or null when the session is missing/expired (401). */
export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: async () => {
      try {
        return (await authApi.me()).user;
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 30_000,
  });
}
