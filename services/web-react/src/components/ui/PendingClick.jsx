import { useEffect, useRef } from 'react';
import { useIsMutating } from '@tanstack/react-query';

/**
 * Marks the button that started the work, for as long as it runs.
 *
 * There are ~200 buttons across ~30 pages. Wiring a `loading` prop into each one
 * guarantees that the next button somebody adds is forgotten — the same reason
 * `BusyProvider` watches mutations globally instead of trusting call sites. So
 * the rule lives here: remember which button was clicked, and while any mutation
 * is in flight, put the spinner on it.
 *
 * Deliberately coarse. It cannot tell which mutation belongs to which button, so
 * with two actions running at once the second click is the one marked. That is
 * still far better than the previous state, where a slow action left the button
 * looking untouched and got clicked again.
 *
 * A button that manages its own state (`components/ui/Button` with `loading`)
 * is left alone: it already knows better than this does.
 */
export function PendingClick() {
  const mutating = useIsMutating();
  const clicked = useRef(null);

  useEffect(() => {
    const onClick = (event) => {
      const button = event.target.closest?.('button');
      // Skip anything that opts out, is already busy, or is not an action:
      // a toolbar filter or a tab must not spin.
      if (!button || button.disabled || button.dataset.noPending !== undefined) return;
      if (button.getAttribute('aria-busy') === 'true') return;
      clicked.current = button;
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, []);

  useEffect(() => {
    const button = clicked.current;
    if (!button) return undefined;
    if (!mutating) {
      button.classList.remove('is-pending');
      clicked.current = null;
      return undefined;
    }
    // Same threshold as the overlay: below it a spinner flashes and vanishes,
    // which reads as a glitch rather than as progress.
    const timer = setTimeout(() => button.classList.add('is-pending'), 250);
    return () => clearTimeout(timer);
  }, [mutating]);

  return null;
}
