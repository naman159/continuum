import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, BrowserRouter as Router, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import CharacterDetail from "./routes/CharacterDetail";
import CharacterList from "./routes/CharacterList";
import Chapters from "./routes/Chapters";
import Continuity from "./routes/Continuity";
import FactionDetail from "./routes/FactionDetail";
import Factions from "./routes/Factions";
import LocationDetail from "./routes/LocationDetail";
import Locations from "./routes/Locations";
import Novels from "./routes/Novels";
import ObjectDetail from "./routes/ObjectDetail";
import Objects from "./routes/Objects";
import Process from "./routes/Process";
import Relationships from "./routes/Relationships";
import SharedDynamics from "./routes/SharedDynamics";
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
          <Route path="/novels/:novelId/dynamics" element={<Layout><SharedDynamics /></Layout>} />
          <Route path="/novels/:novelId/locations" element={<Layout><Locations /></Layout>} />
          <Route path="/novels/:novelId/locations/:locationId" element={<Layout><LocationDetail /></Layout>} />
          <Route path="/novels/:novelId/objects" element={<Layout><Objects /></Layout>} />
          <Route path="/novels/:novelId/objects/:objectId" element={<Layout><ObjectDetail /></Layout>} />
          <Route path="/novels/:novelId/factions" element={<Layout><Factions /></Layout>} />
          <Route path="/novels/:novelId/factions/:factionId" element={<Layout><FactionDetail /></Layout>} />
          <Route path="/novels/:novelId/process" element={<Layout><Process /></Layout>} />
        </Routes>
      </Router>
    </QueryClientProvider>
  );
}
