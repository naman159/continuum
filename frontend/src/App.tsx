import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, BrowserRouter as Router, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import CharacterDetail from "./routes/CharacterDetail";
import CharacterList from "./routes/CharacterList";
import Chapters from "./routes/Chapters";
import Continuity from "./routes/Continuity";
import Novels from "./routes/Novels";
import Relationships from "./routes/Relationships";
import Threads from "./routes/Threads";
import Timeline from "./routes/Timeline";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000 } },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Router>
        <Routes>
          <Route path="/" element={<Navigate to="/novels" replace />} />
          <Route path="/novels" element={<Layout><Novels /></Layout>} />
          <Route path="/novels/:novelId/characters" element={<Layout><CharacterList /></Layout>} />
          <Route path="/novels/:novelId/characters/:characterId" element={<Layout><CharacterDetail /></Layout>} />
          <Route path="/novels/:novelId/chapters" element={<Layout><Chapters /></Layout>} />
          <Route path="/novels/:novelId/timeline" element={<Layout><Timeline /></Layout>} />
          <Route path="/novels/:novelId/threads" element={<Layout><Threads /></Layout>} />
          <Route path="/novels/:novelId/continuity" element={<Layout><Continuity /></Layout>} />
          <Route path="/novels/:novelId/relationships" element={<Layout><Relationships /></Layout>} />
        </Routes>
      </Router>
    </QueryClientProvider>
  );
}
