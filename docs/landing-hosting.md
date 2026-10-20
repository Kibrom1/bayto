# Hosting the landing page

The repository is private. Only the static landing page (`web/landing`, built by `web/vite.landing.config.ts`) is public, hosted on Vercel. The app itself runs only on your own machine and is not hosted.

## One-time setup (Vercel)
1. In Vercel, **Add New → Project**, and import this GitHub repository (authorize access to it).
2. Set **Root Directory** to `web`. Leave the other settings alone: `web/vercel.json` sets the install command, the build command (`npx vite build --config vite.landing.config.ts`) and the output directory (`dist-landing`).
3. Deploy. Every push to `main` redeploys the page; other branches get preview URLs.
4. Optional: add a custom domain under the project's **Settings → Domains**.

## Notes
- The page is built with relative asset paths, so it works at the site root or under a sub-path.
- The page never calls the app's API. The walkthrough videos and screenshots are in `web/public/walkthrough/` and use sample agents.
- Vercel's free Hobby plan is for personal, non-commercial use. If the page is for a commercial product, use a paid plan or another host (Cloudflare Pages and Netlify have free tiers without that limit).
- Preview URLs for pull requests are public by default. Turn on Vercel's deployment protection if a branch should not be visible.
