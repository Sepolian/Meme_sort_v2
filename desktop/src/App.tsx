import "./App.css";

export function App() {
  return (
    <main className="migration-status" aria-labelledby="page-title">
      <section className="status-card">
        <p className="eyebrow">MemeSort desktop</p>
        <h1 id="page-title">Tauri migration workspace</h1>
        <p>
          The desktop shell is ready. The next milestone connects it to the
          authenticated Python sidecar without exposing its local API to the
          WebView.
        </p>
        <dl>
          <div>
            <dt>Frontend</dt>
            <dd>React + TypeScript + Vite</dd>
          </div>
          <div>
            <dt>Host</dt>
            <dd>Tauri 2 (Windows x64)</dd>
          </div>
          <div>
            <dt>Backend</dt>
            <dd>Python sidecar — not connected yet</dd>
          </div>
        </dl>
      </section>
    </main>
  );
}

export default App;
