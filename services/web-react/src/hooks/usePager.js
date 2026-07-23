import { useState } from 'react';

export function usePager(initialPage = 0) {
  const [page, setPage] = useState(initialPage);
  const [hasMore, setHasMore] = useState(false);
  return {
    page,
    hasMore,
    setHasMore,
    next: () => setPage((current) => current + 1),
    prev: () => setPage((current) => Math.max(0, current - 1)),
    reset: () => setPage(0),
  };
}
