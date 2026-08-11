import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, MemoryRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";
import "./modern.css";
import "./brand-v2.css";
import "./growth-path.css";
import "./enhancements.css";
import "./ai-help-fab.css";
import { publicAsset } from "./public-assets";
import { startAnalyticsRuntime } from "./analytics/product-analytics";

const previewRoute = window.location.hash.replace(/^#/, "").split("?")[0];
const isOnboardingPreview = import.meta.env.DEV
  && ["/preview/onboarding", "/preview/g02"].includes(previewRoute);
const previewInitialEntry = previewRoute === "/preview/g02"
  ? "/task/platform-policies"
  : "/";

if (!isOnboardingPreview) startAnalyticsRuntime();

document.documentElement.style.setProperty(
  "--coach-card-background",
  `url("${publicAsset("/assets/backgrounds/coach-card-background.png")}")`,
);

createRoot(document.getElementById("root")).render(
  <StrictMode>
    {isOnboardingPreview ? (
      <MemoryRouter initialEntries={[previewInitialEntry]}>
        <App
          onboardingPreview
          onboardingGuideInitiallyOpen={previewRoute !== "/preview/g02"}
        />
      </MemoryRouter>
    ) : (
      <HashRouter>
        <App />
      </HashRouter>
    )}
  </StrictMode>,
);
