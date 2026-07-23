import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import Topbar from './Topbar';

export default function PlatformLayout() {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="workspace">
        <Topbar />
        <div className="content-stage">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
