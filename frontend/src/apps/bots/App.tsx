// Browser Bots: OpenBot's page as React. The banner and the <main> grid (bot list · gutter · chat · gutter ·
// preview), then the fixed layers (live view, Builds, Apps, dialogs, context menu). Boot order mirrors OpenBot:
// layout first (useLayout, before paint), then the store's poll loop, notifications and the face animator.
import { useEffect } from "react";
import { useThemeSync } from "@platform/lib/theme";
import { AppsPanel } from "./apps/AppsPanel";
import { BuildsPanel } from "./builds/BuildsPanel";
import { BotList } from "./components/BotList";
import { BotMenu } from "./components/BotMenu";
import { ChatPane } from "./components/ChatPane";
import { useFaceAnimator } from "./components/faceAnim";
import { LiveView } from "./components/LiveView";
import { PreviewPane } from "./components/PreviewPane";
import { Dialogs } from "./dialogs/Dialogs";
import { useLayout } from "./hooks/useLayout";
import { installNotify } from "./lib/notify";
import { hideBanner, openDialog, openMenu, openPanel, poll, startStore, useBotsSelector } from "./state/store";

function Banner() {
  const banner = useBotsSelector((s) => s.banner);
  return (
    <div id="banner" className={banner.show ? "show" : ""}>
      <span id="bannerText">{banner.text}</span>
      <button id="bannerBtn" onClick={() => { hideBanner(); void poll(); }}>Retry</button>
    </div>
  );
}

export default function App() {
  useThemeSync();
  const { gutter } = useLayout();
  useEffect(() => startStore(), []);
  useEffect(() => installNotify(), []);
  useFaceAnimator();
  return (
    <>
      <Banner />
      <main>
        <BotList
          onAddBot={() => openDialog({ kind: "newBot" })}
          onOpenUsage={() => openDialog({ kind: "usage" })}
          onOpenBuilds={() => openPanel("builds")}
          onOpenApps={() => openPanel("apps")}
          onContextMenu={(id, x, y) => openMenu({ id, x, y })}
        />
        <div className="gutter l" title="Drag to resize · double-click to reset" {...gutter("l")} />
        <ChatPane />
        <div className="gutter r" title="Drag to resize · double-click to reset" {...gutter("r")} />
        <PreviewPane />
      </main>
      <LiveView />
      <BuildsPanel />
      <AppsPanel />
      <Dialogs />
      <BotMenu />
    </>
  );
}
