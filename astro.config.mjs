// @ts-check
import { defineConfig } from 'astro/config';

// Deployed as a GitHub project page, so `base` has to match the repo name.
// Drop it if the site ever moves to a custom domain or a user-root repo.
export default defineConfig({
  site: 'https://saschahofmann.github.io',
  base: '/barcelona-climate-tracker',
});
