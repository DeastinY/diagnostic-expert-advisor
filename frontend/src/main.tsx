import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { Shell } from "./App";
import { Cohorts } from "./pages/Cohorts";
import { Overview } from "./pages/Overview";
import { Encounters } from "./pages/Encounters";
import { Encounter } from "./pages/Encounter";
import { Profile } from "./pages/Profile";
import { Builder } from "./pages/Builder";
import "./styles.css";

const router = createBrowserRouter([
  { path: "/", element: <Shell />, children: [{ index: true, element: <Cohorts /> }] },
  {
    path: "/c/:name", element: <Shell />,
    children: [
      { index: true, element: <Overview /> },
      { path: "encounters", element: <Encounters /> },
      { path: "encounters/:eid", element: <Encounter /> },
      { path: "profile", element: <Profile /> },
      { path: "build", element: <Builder /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(<StrictMode><RouterProvider router={router} /></StrictMode>);
