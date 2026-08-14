import { useEffect, useRef, useState } from 'react';
import { TablePager } from '../../components/ui/TablePager';
import { usePagination } from '../../hooks/usePagination';
import { PageHeading } from '../../components/layout/PageHeading';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useCostsQuery } from '../../api/queries';
import { formatCount, formatCost } from '../../utils/format';

const COST_PAGE_SIZE = 10;

function costRows(rows, emptyLabel, firstColumn) {
  const entries = Object.entries(rows || {}).sort((a, b) => (b[1].cost_eur || 0) - (a[1].cost_eur || 0));
  if (!entries.length) return <tr><td colSpan={6} className="empty-cell">{emptyLabel}</td></tr>;
  return entries.map(([name, row]) => (
    <tr key={name}>
      <td>{name || firstColumn}</td>
      <td>{formatCount(row.calls)}</td>
      <td>{formatCount(row.input_tokens)}</td>
      <td>{formatCount(row.output_tokens)}</td>
      <td>{formatCount(row.total_tokens)}</td>
      <td>{formatCost(row.cost_eur)}</td>
    </tr>
  ));
}

export default function CostsPage() {
  const { instanceId, currentInstance } = useInstance();
  const { setStatus } = useStatus();
  const [period, setPeriod] = useState('session');
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const query = useCostsQuery(period);
  const summary = query.data?.summary;
  const entries = query.data?.entries || [];
  const totals = summary?.totals || {};

  useEffect(() => {
    if (!query.data) return;
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Coûts chargés.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger les coûts : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const unpriced = summary?.unpriced_models || [];
  const entryPager = usePagination(entries, COST_PAGE_SIZE);

  useEffect(() => {
    entryPager.setPage(0);
  }, [period]); // eslint-disable-line react-hooks/exhaustive-deps

  const modelRows = Object.entries(summary?.by_model || {}).sort((a, b) => (b[1].calls || 0) - (a[1].calls || 0));
  const currentModel = modelRows[0]?.[0] || entries[0]?.model || 'unknown';
  const pageEntries = entryPager.visible;

  return (
    <>
      <PageHeading view="costs" />
      <div className="toolbar">
        <select aria-label="Période des coûts" value={period} onChange={(e) => setPeriod(e.target.value)}>
          <option value="session">Session</option>
          <option value="day">Aujourd'hui</option>
          <option value="month">Mois</option>
        </select>
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Charger les coûts</span>
        </button>
        <span className="counter">{currentInstance?.display_name || summary?.agent_instance_id || instanceId}</span>
      </div>
      <div className="cost-summary-grid">
        <div><strong>{currentModel}</strong><span>Modèle actuel</span></div>
        <div>
          <strong>{formatCost(totals.cost_eur)}</strong>
          <span>Dépense totale</span>
          <small className="metric-hint">
            {unpriced.length
              ? `Estimation incomplète : aucun tarif pour ${unpriced.join(', ')}`
              : 'Modèles locaux : coût de calcul estimé, pas une facture fournisseur.'}
          </small>
        </div>
        <div><strong>{formatCount(totals.calls)}</strong><span>Appels LLM</span></div>
        <div><strong>{formatCount(totals.input_tokens)}</strong><span>Tokens entrée</span></div>
        <div><strong>{formatCount(totals.output_tokens)}</strong><span>Tokens sortie</span></div>
      </div>
      <div className="cost-layout">
        <div>
          <h2 className="section-title">Par modèle</h2>
          <div className="table-wrap cost-table-wrap">
            <table className="data-table cost-table">
              <thead><tr><th>Modèle</th><th>Appels</th><th>Entrée</th><th>Sortie</th><th>Total</th><th>EUR</th></tr></thead>
              <tbody>{costRows(summary?.by_model, 'Aucun coût par modèle enregistré.', 'model')}</tbody>
            </table>
          </div>
        </div>
        <div>
          <h2 className="section-title">Par nœud</h2>
          <div className="table-wrap cost-table-wrap">
            <table className="data-table cost-table">
              <thead><tr><th>Nœud</th><th>Appels</th><th>Entrée</th><th>Sortie</th><th>Total</th><th>EUR</th></tr></thead>
              <tbody>{costRows(summary?.by_node, 'Aucun coût par nœud enregistré.', 'node')}</tbody>
            </table>
          </div>
        </div>
      </div>
      <div className="cost-recent">
        <h2 className="section-title">Appels récents</h2>
        <div className="table-wrap cost-table-wrap cost-recent-table-wrap">
          <table className="data-table cost-table cost-recent-table">
            <thead>
              <tr><th>Heure</th><th>Nœud</th><th>Modèle</th><th>Run</th><th>Tokens in/out/total</th><th>EUR</th></tr>
            </thead>
            <tbody>
              {pageEntries.length ? pageEntries.map((entry, i) => (
                <tr key={`${entry.run_id || ''}-${entry.timestamp || i}`}>
                  <td>{entry.timestamp || ''}</td>
                  <td><span className="status-pill">{entry.node || 'unknown'}</span></td>
                  <td>{entry.model || 'unknown'}</td>
                  <td>{entry.run_id || ''}</td>
                  <td>{formatCount(entry.input_tokens)} / {formatCount(entry.output_tokens)} / {formatCount(entry.total_tokens)}</td>
                  <td>{formatCost(entry.cost_eur)}</td>
                </tr>
              )) : <tr><td colSpan={6} className="empty-cell">Aucune entrée de coût enregistrée.</td></tr>}
            </tbody>
          </table>
        </div>
        <TablePager
          className="cost-table-pager"
          page={entryPager.page}
          pageCount={entryPager.pageCount}
          total={entryPager.total}
          size={entryPager.size}
          onPage={entryPager.setPage}
          onSize={entryPager.setSize}
          unit="appels"
        />
      </div>
    </>
  );
}
