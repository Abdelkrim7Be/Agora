import { createContext, useContext, useCallback, useEffect, useRef, useState } from 'react';

const DialogContext = createContext(null);

const DEFAULTS = {
  title: 'Confirmer l’action',
  message: '',
  confirmLabel: 'Continuer',
  cancelLabel: 'Annuler',
  confirmIcon: 'check',
  variant: '',
  kind: 'confirm', // 'confirm' | 'prompt'
  placeholder: '',
  defaultValue: '',
  required: true,
};

export function DialogProvider({ children }) {
  const [dialog, setDialog] = useState(null); // merged options while open, else null
  const [value, setValue] = useState('');
  const [inputError, setInputError] = useState(false);
  const resolveRef = useRef(null);

  useEffect(() => {
    const shell = document.querySelector('.app-shell');
    if (!shell) return;
    if (dialog) {
      shell.setAttribute('aria-hidden', 'true');
      shell.inert = true;
    } else {
      shell.removeAttribute('aria-hidden');
      shell.inert = false;
    }
  }, [dialog]);

  const open = useCallback((options) => {
    return new Promise((resolve) => {
      resolveRef.current = resolve;
      setValue(options.defaultValue || '');
      setInputError(false);
      setDialog({ ...DEFAULTS, ...options });
    });
  }, []);

  const close = useCallback((result) => {
    setDialog(null);
    const resolve = resolveRef.current;
    resolveRef.current = null;
    if (resolve) resolve(result);
  }, []);

  const submit = useCallback(() => {
    if (dialog?.kind === 'prompt') {
      const trimmed = value.trim();
      if (dialog.required && !trimmed) {
        setInputError(true);
        return;
      }
      close(trimmed);
      return;
    }
    close(true);
  }, [dialog, value, close]);

  const confirmDialog = useCallback((options) => open({ ...options, kind: 'confirm' }), [open]);
  const promptDialog = useCallback((options) => open({ ...options, kind: 'prompt' }), [open]);

  return (
    <DialogContext.Provider value={{ confirmDialog, promptDialog }}>
      {children}
      <div id="app-dialog" className={`modal app-dialog${dialog?.variant === 'danger' ? ' dialog-danger' : ''}`} hidden={!dialog}>
        {dialog && (
          <>
            <div className="modal-backdrop" onClick={() => close(null)}></div>
            <section className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
              <div className="modal-header">
                <div>
                  <h2 id="dialog-title">{dialog.title}</h2>
                  <p id="dialog-message">{dialog.message}</p>
                </div>
                <button className="ghost icon-button" type="button" aria-label="Close dialog" onClick={() => close(null)}>
                  <span className="material-symbols-outlined" aria-hidden="true">close</span>
                </button>
              </div>
              {dialog.kind === 'prompt' && (
                <textarea
                  rows={dialog.multiline ? 5 : 1}
                  placeholder={dialog.placeholder}
                  value={value}
                  className={inputError ? 'input-error' : ''}
                  onChange={(event) => { setValue(event.target.value); setInputError(false); }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) submit();
                  }}
                  autoFocus
                />
              )}
              <div className="dialog-actions">
                <button className="ghost" type="button" onClick={() => close(null)}>{dialog.cancelLabel}</button>
                <button className={dialog.variant === 'danger' ? 'danger' : 'primary'} type="button" onClick={submit} autoFocus={dialog.kind !== 'prompt'}>
                  <span className="material-symbols-outlined" aria-hidden="true">{dialog.confirmIcon}</span>
                  <span>{dialog.confirmLabel}</span>
                </button>
              </div>
            </section>
          </>
        )}
      </div>
    </DialogContext.Provider>
  );
}

export const useDialog = () => useContext(DialogContext);
