# UX round 2 (PR #211)

What PR #211 changed, grouped by page. Findings come from `docs/ux-reverify-2026-10-23.md`; `UX-xx` were "Partly fixed" after round 1, `N1`-`N8` were new in the re-check.

## Findings by page

```mermaid
flowchart LR
  RV["Re-verification<br/>UX-08, 11, 12, 18, 19, 24, 26, 27<br/>N1 to N8"]

  subgraph Room["Room page: BaytoRoom.tsx"]
    R1["Stop reasons and mode names match<br/>ignoring case and separators<br/>so Build complete shows the green banner"]
    R2["Continue as Build is a plain button<br/>amber is for warnings only (N3)"]
    R3["Ended room never shows<br/>N new turns (N6)"]
    R4["Tab title is the task title"]
  end

  subgraph Rail["Room rail: RoomRail.tsx"]
    A1["id room-rail matches aria-controls (N1)"]
    A2["Rail is keyboard focusable (N2)"]
    A3["No duplicate landmark names (N4)"]
    A4["Empty moderator summary hidden"]
    A5["Roster phrases never break mid-phrase (N8)"]
  end

  subgraph Board["Task board: TaskBoard.tsx"]
    B1["Labels match filters: Finished<br/>plus Paused, Failed, Interrupted"]
    B2["Paused no longer counted as Live"]
  end

  subgraph New["New task and setup"]
    N1["Retrieval switch uses the plain label<br/>Search long documents by meaning"]
    N2["Hint says text goes to an embedding provider"]
    N3["Mode names looked up in one form<br/>Raise Hand = Open Chat"]
  end

  subgraph Shell["Shared: components.css, BaytoRoom.css"]
    S1["Top nav no longer wraps at 390 (N5)"]
    S2["Larger touch targets (N7)"]
  end

  RV --> Room
  RV --> Rail
  RV --> Board
  RV --> New
  RV --> Shell

  OUT["Verified: tsc, vitest, axe clean<br/>running, ended and Build rooms<br/>1280 and 390, light and dark"]
  Room --> OUT
  Rail --> OUT
  Board --> OUT
  New --> OUT
  Shell --> OUT

  classDef ok fill:#e6f4ea,stroke:#2e7d32,color:#1b5e20
  class OUT ok
```

## Finished room before and after

```mermaid
flowchart LR
  subgraph Before
    b1["Reason build-complete<br/>not recognised"] --> b2["Generic grey banner"]
    b3["Finished room"] --> b4["Amber Continue as Build<br/>plus N new turns pill"]
  end
  subgraph After
    a1["Reason normalised<br/>lower case, separators folded"] --> a2["Green banner: tests passed,<br/>reviewer approved"]
    a3["Finished room"] --> a4["Plain Continue as Build<br/>no new-turns pill"]
  end
  classDef ok fill:#e6f4ea,stroke:#2e7d32,color:#1b5e20
  class a2,a4 ok
```

## Files touched

`web/src/pages/`: `BaytoRoom`, `TaskBoard`, `NewTaskForm`, `SessionSetup` (+ tests, `BaytoRoom.css`).
`web/src/components/`: `RoomRail` (+ css, test). `web/src/components.css`, `web/src/api/tasks.ts`, `docs/decisions.md`.
