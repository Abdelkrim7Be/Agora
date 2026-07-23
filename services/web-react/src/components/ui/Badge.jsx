import { healthClass, statusLabelFr } from '../../utils/format';

export function StatusBadge({ status, label, classFn = healthClass }) {
  return <span className={`status-pill ${classFn(status)}`}>{label ?? statusLabelFr(status)}</span>;
}

export function Badge({ children }) {
  return <span className="badge">{children}</span>;
}
