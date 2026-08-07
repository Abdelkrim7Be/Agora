import { useEffect, useRef, useState } from 'react';
import { TablePager } from '../../components/ui/TablePager';
import { usePagination } from '../../hooks/usePagination';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useStatus } from '../../contexts/StatusContext';
import { useDlqQuery, useRequeueDlqEntry } from '../../api/queries';
import { statusLabelFr } from '../../utils/format';

export default function DlqPage() {
  const { setStatus } = useStatus();
  const [requeuingId, setRequeuingId] = useState('');
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const query = useDlqQuery();
  const requeueEntry = useRequeueDlqEntry();
  const entries = query.data?.entries || [];
  const pager = usePagination(entries);

  useEffect(() => {
    if (!query.data) return;
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      const count = entries.length;
      setStatus(`${count} entrée${count === 1 ? '' : 's'} DLQ chargée${count === 1 ? '' : 's'}.`, 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger la DLQ : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRequeue = async (entryId) => {
    setRequeuingId(entryId);
    setStatus('Relance DLQ en cours...');
    try {
      await requeueEntry.mutateAsync(entryId);
      setStatus('Entrée DLQ relancée.', 'ok');
    } catch (error) {
      setStatus(`Impossible de relancer l'entrée DLQ : ${error.message}`, 'error');
    } finally {
      setRequeuingId('');
    }
  };

  return (
    <>
      <PageHeading view="dlq" />
      <Card>
        <div className="card-header">
          <div>
            <h2>File des échecs (DLQ)</h2>
            <div className="meta"><span>Emails en échec relançables une seule fois</span></div>
          </div>
          <button type="button" onClick={() => query.refetch()}>Actualiser</button>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Heure</th><th>Message</th><th>Motif</th><th>Erreur</th><th>Statut</th><th></th></tr></thead>
            <tbody>
              {query.error ? (
                <tr><td colSpan={6} className="empty-cell">{`DLQ indisponible : ${query.error.message}`}</td></tr>
              ) : !entries.length ? (
                <tr><td colSpan={6} className="empty-cell">Aucune entrée DLQ.</td></tr>
              ) : pager.visible.map((entry) => (
                <tr key={entry.entry_id}>
                  <td>{entry.timestamp || 'n/d'}</td>
                  <td>{entry.message_id || entry.entry_id || ''}</td>
                  <td>{entry.reason || ''}</td>
                  <td>{entry.error || ''}</td>
                  <td><span className={`status-pill ${entry.status === 'dead_letter' ? 'error' : 'warn'}`}>{statusLabelFr(entry.status)}</span></td>
                  <td>
                    <button
                      type="button"
                      disabled={entry.status !== 'dead_letter' || requeuingId === entry.entry_id}
                      onClick={() => handleRequeue(entry.entry_id)}
                    >
                      {requeuingId === entry.entry_id ? 'Relance...' : 'Relancer'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {entries.length ? (
          <TablePager
            page={pager.page}
            pageCount={pager.pageCount}
            total={pager.total}
            size={pager.size}
            onPage={pager.setPage}
            onSize={pager.setSize}
            unit="entrées"
          />
        ) : null}
      </Card>
    </>
  );
}
