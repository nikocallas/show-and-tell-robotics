import { BrowserRouter, Link, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { buttonClass, EmptyState } from "./components/ui";
import { ModeProvider, useMode } from "./mode";
import { BenchPage } from "./pages/Bench";
import { DemoIndexPage } from "./pages/DemoIndex";
import { JobPage, ReplayPage } from "./pages/Job";
import { NewTaskPage } from "./pages/NewTask";

/** Demo mode opens on the recorded runs; live mode on a new task. */
function Home() {
  const { mode } = useMode();
  return mode === "live" ? <NewTaskPage /> : <DemoIndexPage />;
}

function NotFound() {
  return (
    <EmptyState
      title="Page not found"
      action={
        <Link to="/" className={buttonClass("primary", "sm")}>
          Go home
        </Link>
      }
    >
      This address doesn't match anything in the app.
    </EmptyState>
  );
}

export default function App() {
  return (
    <ModeProvider>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Layout>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/new" element={<NewTaskPage />} />
            <Route path="/runs" element={<DemoIndexPage />} />
            <Route path="/jobs/:id" element={<JobPage />} />
            <Route path="/replay/:id" element={<ReplayPage />} />
            <Route path="/bench" element={<BenchPage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </ModeProvider>
  );
}
