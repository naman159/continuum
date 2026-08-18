import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, BrowserRouter as Router, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Canon from "./routes/Canon";
import CharacterDetail from "./routes/CharacterDetail";
import CharacterList from "./routes/CharacterList";
import Chapters from "./routes/Chapters";
import Commitments from "./routes/Commitments";
import Continuity from "./routes/Continuity";
import CustomEntityDetail from "./routes/CustomEntityDetail";
import CustomEntityList from "./routes/CustomEntityList";
import EntityGraph from "./routes/EntityGraph";
import FactionDetail from "./routes/FactionDetail";
import Factions from "./routes/Factions";
import Knowledge from "./routes/Knowledge";
import LocationDetail from "./routes/LocationDetail";
import Locations from "./routes/Locations";
import NotFound from "./routes/NotFound";
import Novels from "./routes/Novels";
import ObjectDetail from "./routes/ObjectDetail";
import Objects from "./routes/Objects";
import Process from "./routes/Process";
import Relationships from "./routes/Relationships";
import Scenes from "./routes/Scenes";
import Search from "./routes/Search";
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
          <Route path="/novels/:novelId/search" element={<Layout><Search /></Layout>} />
          <Route path="/novels/:novelId/characters" element={<Layout><CharacterList /></Layout>} />
          <Route path="/novels/:novelId/characters/:characterId" element={<Layout><CharacterDetail /></Layout>} />
          <Route path="/novels/:novelId/chapters" element={<Layout><Chapters /></Layout>} />
          <Route path="/novels/:novelId/timeline" element={<Layout><Timeline /></Layout>} />
          <Route path="/novels/:novelId/threads" element={<Layout><Threads /></Layout>} />
          <Route path="/novels/:novelId/continuity" element={<Layout><Continuity /></Layout>} />
          <Route path="/novels/:novelId/relationships" element={<Layout><Relationships /></Layout>} />
          <Route path="/novels/:novelId/dynamics" element={<Layout><SharedDynamics /></Layout>} />
          <Route path="/novels/:novelId/entity-graph" element={<Layout><EntityGraph /></Layout>} />
          <Route path="/novels/:novelId/locations" element={<Layout><Locations /></Layout>} />
          <Route path="/novels/:novelId/locations/:locationId" element={<Layout><LocationDetail /></Layout>} />
          <Route path="/novels/:novelId/objects" element={<Layout><Objects /></Layout>} />
          <Route path="/novels/:novelId/objects/:objectId" element={<Layout><ObjectDetail /></Layout>} />
          <Route path="/novels/:novelId/factions" element={<Layout><Factions /></Layout>} />
          <Route path="/novels/:novelId/factions/:factionId" element={<Layout><FactionDetail /></Layout>} />
          <Route path="/novels/:novelId/scenes" element={<Layout><Scenes /></Layout>} />
          <Route path="/novels/:novelId/commitments" element={<Layout><Commitments /></Layout>} />
          <Route path="/novels/:novelId/canon" element={<Layout><Canon /></Layout>} />
          <Route path="/novels/:novelId/knowledge" element={<Layout><Knowledge /></Layout>} />
          <Route path="/novels/:novelId/process" element={<Layout><Process /></Layout>} />
          <Route path="/novels/:novelId/entity-types/:typeName/entities" element={<Layout><CustomEntityList /></Layout>} />
          <Route path="/novels/:novelId/custom-entities/:entityId" element={<Layout><CustomEntityDetail /></Layout>} />
          {/* A bare /novels/:novelId has no page of its own; send it to the
              novel's characters rather than rendering nothing. */}
          <Route path="/novels/:novelId" element={<Navigate to="characters" replace />} />
          {/* Without a catch-all, an unmatched URL renders zero routes: a
              blank page with no sidebar and no way back. */}
          <Route path="*" element={<Layout><NotFound /></Layout>} />
        </Routes>
      </Router>
    </QueryClientProvider>
  );
}
