import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, BrowserRouter as Router, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import CharacterDetail from "./routes/CharacterDetail";
import CharacterList from "./routes/CharacterList";
import Novels from "./routes/Novels";

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
        </Routes>
      </Router>
    </QueryClientProvider>
  );
}
