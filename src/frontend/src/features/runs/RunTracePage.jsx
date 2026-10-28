import { Link, useParams } from 'react-router-dom';
import { RUN_KINDS } from './state';
import { RunTraceContent } from './TraceWaterfall';

export default function RunTracePage() {
  const { kind, runId } = useParams();
  const validRoute = RUN_KINDS.includes(kind) && Boolean(runId);

  if (!validRoute) return <main className="runs-page runs-trace-page"><div className="runs-state">This run link is invalid. <Link to="/runs">Return to runs</Link>.</div></main>;
  return <main className="runs-page runs-trace-page" aria-label="Run trace">
    <Link className="runs-back" to="/runs">← Runs</Link>
    <RunTraceContent kind={kind} runId={runId} />
  </main>;
}
