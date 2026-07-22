import { createContext, useContext, useState, useEffect, useCallback } from 'react';

const I18nContext = createContext(null);

export function I18nProvider({ children }) {
  const [dict, setDict] = useState({});
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    fetch('/i18n/fr.json')
      .then(r => r.json())
      .then(data => {
        setDict(data);
        setLoaded(true);
      })
      .catch(e => {
        console.error('Failed to load fr.json', e);
        setLoaded(true); // Proceed even if it fails
      });
  }, []);

  const t = useCallback((key, vars = {}) => {
    let str = key.split('.').reduce((o, i) => o?.[i], dict);
    if (str === undefined) {
      return key;
    }
    return str.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? `{${k}}`);
  }, [dict]);

  // Don't render until dict is loaded to avoid initial flash of raw keys
  if (!loaded) return null;

  return <I18nContext.Provider value={{ t }}>{children}</I18nContext.Provider>;
}

export const useI18n = () => useContext(I18nContext);
