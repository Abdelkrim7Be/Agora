import { useId, useRef, useState } from 'react';

/**
 * A file picker in French.
 *
 * The native control paints its own button, labelled by the browser locale —
 * so a fully translated page still showed "Choose File / No file chosen". The
 * real input stays in the DOM (same events, same accept filter, same form
 * semantics) and is visually hidden behind a button we do control.
 */
export function FileField({
  accept,
  onChange,
  label = 'Choisir un fichier',
  disabled = false,
  inputRef,
  ...rest
}) {
  const generatedId = useId();
  const fallbackRef = useRef(null);
  const ref = inputRef || fallbackRef;
  const [fileName, setFileName] = useState('');

  const handleChange = (event) => {
    setFileName(event.target.files?.[0]?.name || '');
    onChange?.(event);
  };

  return (
    <div className="file-field">
      <input
        {...rest}
        id={generatedId}
        ref={ref}
        className="file-field-input"
        type="file"
        accept={accept}
        disabled={disabled}
        onChange={handleChange}
      />
      <label className="file-field-button" htmlFor={generatedId}>
        <span className="material-symbols-outlined" aria-hidden="true">upload_file</span>
        <span>{label}</span>
      </label>
      <span className="file-field-name">{fileName || 'Aucun fichier sélectionné'}</span>
    </div>
  );
}
