import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles.css';
// After styles.css so utilities win over the hand-written rules on migrated pages.
import './tailwind.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
