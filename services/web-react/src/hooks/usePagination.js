import { useEffect, useMemo, useState } from 'react';

export const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

/**
 * Client-side paging for lists the API already returns whole.
 *
 * Every table in the app rendered its full result set, so a mailbox backlog or
 * a month of notifications came out as one endless scroll with no way back to
 * the top of the list.
 */
export function usePagination(items, initialSize = 25) {
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(initialSize);

  const total = items.length;
  const pageCount = Math.max(1, Math.ceil(total / size));

  // Deleting the last row of the last page, or narrowing a filter, must not
  // leave the view parked past the end showing nothing.
  useEffect(() => {
    if (page > pageCount - 1) setPage(pageCount - 1);
  }, [page, pageCount]);

  const visible = useMemo(
    () => items.slice(page * size, page * size + size),
    [items, page, size],
  );

  const changeSize = (next) => {
    setSize(next);
    setPage(0);
  };

  return { page, setPage, size, setSize: changeSize, total, pageCount, visible };
}
