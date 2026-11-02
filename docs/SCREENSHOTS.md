# Screenshot provenance

Captured: 2026-11-02. Assets are real captures of the compiled CAM React frontend, not generated product mockups.

| Asset | What it shows | Data and limits |
| --- | --- | --- |
| [CAM overview](assets/cam-overview.jpg) | Readable navigation, workspace shortcuts and orb | Fixture HTTP responses; mock provider; no model inference |
| [First-run setup](assets/cam-setup.jpg) | Gemini, Claude and NVIDIA choice, plus Set up later | Simulated native bridge; no real keys, database or provider requests |
| [CAM banner](assets/cam-banner.svg) | Original vector brand artwork | Decorative illustration, not an application screenshot |

The production frontend was served on a temporary loopback-only documentation server. An isolated page fixture supplied the desktop session/configuration contract and intercepted fetches with synthetic responses. The fixture never launched the application backend, opened personal persistence, submitted credentials or contacted a live AI provider. Rendered API availability labels describe those fixture responses only.

The original JPEG captures are included without retouching. They show interface behavior and layout; they do not establish native packaging, real provider inference, browser isolation or full release acceptance. See [publication verification](PUBLICATION_VERIFICATION.md).
