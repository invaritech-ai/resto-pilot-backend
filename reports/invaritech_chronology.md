# Invaritech-AI Repo Chronology
Generated: 2026-02-11T18:05:09Z

## CCC-redesign
- default branch: main
- dev branch: (none)

### Commits (main) - oldest -> newest
- [2025-10-24T05:01:22Z] `23cfda4` [skip lovable] Use tech stack vite_react_shadcn_ts_20250728_minor
- [2025-10-24T05:04:59Z] `07306a0` Copy website content
- [2025-10-24T06:03:52Z] `e902e08` Add About, Future, and Contact pages
- [2025-11-12T09:53:52Z] `810cfe3` Add Community, Events, and related pages; update favicon and logo; enhance styles with new color palette
- [2025-11-12T10:44:39Z] `47c5d58` Implement font optimization by self-hosting fonts, enhance route loading with lazy loading, and improve navigation styles. Update Vite configuration for better chunk management and build optimization.
- [2025-11-14T06:57:32Z] `217c521` Add Sanity CMS setup instructions and integrate new components for enhanced site functionality. Implement DonateNowButton, FeaturedStorySection, and KeyHighlightsSection for improved user engagement. Update existing components to utilize new features and streamline the user interface.
- [2025-11-14T10:22:17Z] `74a5218` Add Sanity schemas for case studies, events, FAQs, galleries, partners, press releases, reports, resources, team members, testimonials, and updates. Integrate new queries for fetching data and update components to utilize Sanity data for dynamic content rendering.
- [2025-11-14T13:18:00Z] `b5f9e59` Add homepage review document outlining issues and changes needed across various sections. Include critical, medium, and low priority recommendations for improvements. Also, add Sanity sample data guide for team members, testimonials, events, updates, case studies, partners, FAQs, resources, reports, and press releases to assist with content entry.
- [2025-11-14T14:00:25Z] `5207fbc` Remove outdated documentation files including HOMEPAGE_REVIEW.md, SANITY_SAMPLE_DATA.md, and SANITY_SETUP.md. Update README.md to reflect project overview, features, tech stack, and installation instructions. Enhance components with new carousel functionality for Featured Story, Testimonials, and Noticeboard sections, improving user engagement and visual presentation. Add infinite scrolling feature for smoother content display.
- [2025-11-14T15:30:40Z] `2e6f1f7` Update Tailwind configuration and styles: add new logo-text color variable, adjust primary color values, and modify component styles for improved visual consistency. Enhance HighlightCard and Logo components to utilize the new color variable.
- [2025-11-14T15:59:47Z] `cd85341` Enhance accessibility and SEO: Add skip link for keyboard navigation, preload hero image, and include structured data for SEO optimization. Update various components to improve visual consistency and user experience, including aria attributes for icons and buttons.
- [2025-11-14T16:32:39Z] `3832d27` Update hero image format to WebP for better performance, enhance accessibility by adding aria-hidden attributes to various icons, and improve component styles for consistency across the application.
- [2025-11-14T16:42:22Z] `dd58f3f` Refactor components to improve type safety and enhance performance: Update FeaturedStorySection, NoticeboardSection, TestimonialsSection, and TrustSignalsSection to utilize Sanity types. Optimize image handling with new utility functions for better image URL management. Update README.md for clarity on project features and installation instructions. Enhance Tailwind configuration with new color variables and animation settings for improved UI consistency.
- [2025-11-14T17:01:41Z] `40d5608` Add Cookie Banner component for cookie consent management: Integrate CookieBanner into App, implement useCookieBanner hook for visibility control, and update Privacy page with detailed cookie usage information. Enable CDN for improved performance while ensuring compliance with privacy standards.
- [2025-11-14T17:03:59Z] `b5597a2` Add Vercel configuration for URL rewrites: Create vercel.json to redirect all requests to index.html, enabling single-page application routing.
- [2025-11-17T01:50:52Z] `79cdff4` Add new About page components: Introduce AboutHero, AboutIntroduction, AboutHistory, CommunityMembersSection, TeamMembersSection, AnnualReportsSection, and CoreValuesSection to enhance the About page layout and content presentation. Implement data fetching for team members and reports, improving user engagement and information accessibility.
- [2025-11-17T14:13:33Z] `341c412` Add placeholder SVG images for future project visualizations: Create six SVG files representing different aspects of the reconstruction project, each featuring a descriptive text element. Update the Future page to include these images in the project gallery, enhancing visual representation of the initiative.
- [2025-11-18T04:33:00Z] `7028395` Implement dynamic form handling and page content rendering: Introduce DynamicForm and PageContent components to manage form submissions and display page content dynamically. Integrate Google Sheets API for form data storage and enhance the Community page with new content structure. Update sanity schemas for form builder and page content, ensuring robust data management and user interaction.
- [2025-11-18T04:39:54Z] `137a80a` Update Vercel configuration to include additional URL rewrite rule: Redirect all requests to index.html for improved single-page application routing.
- [2025-11-18T04:40:09Z] `33816e6` Refactor Vercel configuration for improved readability: Adjust formatting in vercel.json for better clarity while maintaining existing URL rewrite rules and build settings.
- [2025-11-18T04:44:34Z] `99ffaae` Refactor Vercel configuration to enhance URL rewrite rules: Update the destination pattern for non-API requests in vercel.json, improving routing for single-page applications while maintaining existing build settings.
- [2025-11-18T04:50:50Z] `eb38ec5` Enhance file upload handling and logging in form submission API: Update uploadFileToBlob function to return structured responses with error handling. Improve logging for request processing, environment variable checks, and file uploads in the submit-form API. Adjust DynamicForm component to reflect maximum file size dynamically based on imported constant.
- [2025-11-18T04:56:24Z] `fe7aa9c` Update file upload handling in submit-form API: Modify uploadFileToBlob function to accept a token parameter for improved security. Change environment variable reference from BLOB_READ_WRITE_TOKEN to CCC_READ_WRITE_TOKEN, enhancing clarity in error logging and ensuring correct token usage for file uploads.
- [2025-11-18T05:05:17Z] `f323201` Refactor header management in submit-form API: Streamline the process of creating and updating headers based on form fields and file links. Enhance logging for header creation and value array building, ensuring accurate data handling during form submissions.
- [2025-11-18T05:10:03Z] `52a7050` Refactor submit-form API for improved file upload and header management: Streamline the process of handling file uploads and enhance error handling in uploadFileToBlob. Introduce helper functions for managing headers and building values arrays, ensuring accurate data submission to Google Sheets. Improve logging for better traceability during form submissions.
- [2025-11-18T05:23:31Z] `8ec7088` Implement dynamic form integration across multiple pages: Add DynamicForm component to Contact, Events, Privacy, Reports, SupportDonate, Updates, Volunteer, and Waitlist pages. Fetch form configurations using getFormByPage for each respective page, enhancing user interaction and data submission capabilities. Remove unused page options from formBuilder schema.
- [2025-11-18T05:33:29Z] `9c4039c` Enhance page content management and SEO for multiple pages: Integrate PageContent component into Events, Reports, SupportDonate, Updates, and Volunteer pages. Fetch page-specific content using getPageContent, improving user experience and SEO. Implement dynamic meta tag updates for better search engine visibility and user engagement.
- [2025-11-18T06:02:13Z] `9d2eb8a` Refactor EventDetail, UpdateDetail, Events, Reports, and Updates pages for improved layout and user experience: Replace static loading indicators with animated spinners, enhance section styling for better visual appeal, and update content structure for clarity. Ensure consistent design across pages with responsive text and layout adjustments.
- [2025-11-18T06:25:12Z] `8578fc4` Add Search and Filter component to Events, Reports, and Updates pages: Implement search functionality and filter options for categories, dates, and types. Enhance user experience with debounced search input and clear filters button. Update layout for better content display and pagination controls.
- [2025-11-18T07:44:17Z] `ed95b2e` Enhance DynamicForm component with inline rendering option and improved empty state handling: Add an optional inline prop to render the form without additional section styling. Refactor empty state display for better code organization and user experience. Update Contact page to utilize the new inline feature for dynamic forms.
- [2025-11-18T07:54:39Z] `9f11575` Add content link styles to index.css and update PortableText component: Introduce a new utility class for content links to enhance styling consistency across content areas. Update the PortableText component to utilize the new class for improved link presentation.
- [2025-11-18T12:09:01Z] `12dbeed` Add WhatsApp button component and integrate into App and Footer: Introduce a new WhatsAppButton component for easy messaging. Update App and Footer components to include the button, enhancing user engagement. Add react-icons dependency for icon support.
- [2025-11-24T15:24:56Z] `7fc38c8` Add CMSPage component and update routing structure: Introduce a new CMSPage component for dynamic content rendering and form integration. Update App and Navigation components to reflect new routes for CMS pages, enhancing content management and user navigation. Refactor navigation structure for improved accessibility and organization.
- [2025-11-24T15:34:35Z] `3ddd2c4` Add new About sections and components for enhanced content presentation: Introduce AboutCommitmentSection, AboutCTASection, AboutRedevelopmentSection, AboutUniqueIdentity, and PullQuote components to improve the About page layout and user engagement. Update About page to utilize these new components, providing a clearer narrative and call to action for users.
- [2025-11-24T16:11:18Z] `4a45e24` Refactor formBuilder schema and enhance PortableText component with timeline support: Update the formBuilder schema to clarify the page slug description. Implement timeline parsing and rendering in the PortableText component, introducing a new Timeline component for structured content presentation. Adjust CMSPage to ensure correct slug handling for dynamic content.
- [2025-11-24T17:06:43Z] `901c025` Enhance page content schema and components for improved layout and functionality: Update the pageContent schema to include hero images and bottom images, enhancing visual presentation. Refactor PageContent and PortableText components to support new image fields and integrate a dynamic Grid component for structured content display. Update CMSPage to utilize the new properties, improving content management and user experience.
- [2025-11-24T17:45:20Z] `a8f62a1` Add Board Governance page and related components for enhanced governance presentation: Introduce BoardGovernance, BoardGovernanceHero, HowWeAreGovernedSection, GovernanceStructureSection, CommitteeSection, and AccountabilityComplianceSection components. Update App routing to include the new Board Governance page, improving content organization and user navigation. Add organizational chart image for visual representation of governance structure.
- [2025-11-24T17:49:25Z] `0582c20` Add shorter route aliases for reports in App component: Introduce '/reports' and '/reports/:slug' routes for easier access to the Reports and ReportDetail components, improving navigation efficiency.
- [2025-11-24T17:52:55Z] `c697619` Update pageContent schema to include media in prepare function: Modify the prepare function to accept and return media alongside alt text, enhancing image handling in the schema.
- [2025-11-24T18:02:51Z] `512e3cf` Add new images for the Future page and update image gallery: Introduce multiple new image files for the Future page, enhancing visual content. Update the ImageGallery component to support keyboard navigation and add previous/next buttons for improved user experience. Modify the Future page to utilize the new images and adjust the timeline phases for accuracy.
- [2025-11-24T18:14:05Z] `bfe2903` Add new sections for Future page: Introduce GovernanceTeamSection, HowCareContinuesSection, HowToSupportSection, WhatIsBeingBuiltSection, WhereResidentsAreNowSection, and WhyRedevelopingSection components to enhance content presentation. Update Future page to incorporate these new sections, improving user engagement and information clarity regarding the redevelopment project.
- [2025-11-24T18:27:30Z] `c272d86` Add new images for the Future page and update components: Introduce multiple new .webp image files for enhanced visual content on the Future page. Update HowCareContinuesSection, HowToSupportSection, WhatIsBeingBuiltSection, WhereResidentsAreNowSection, and WhyRedevelopingSection components to incorporate new icons and improve layout, ensuring a more engaging user experience.
- [2025-11-24T18:35:47Z] `2d25595` Update navigation links across multiple components for improved routing: Change links in DonateNowButton, FeaturedStorySection, Footer, HeroSection, KeyHighlightsSection, NoticeboardSection, and ServicesSection to reflect updated paths, enhancing user navigation and content accessibility.
- [2025-11-24T18:40:06Z] `6566b28` Add RedevelopmentBanner component and update HeroSection: Introduce a new RedevelopmentBanner component for improved visibility of redevelopment information. Update HeroSection to enhance layout and styling, incorporating a new icon and adjusting padding for better responsiveness.
- [2025-11-24T19:02:06Z] `7fc5713` Update Navigation and Sheet components for improved styling and z-index management: Adjust header and button styles in Navigation for better visibility and consistency. Modify SheetOverlay and SheetContent to enhance layering and ensure proper display in the UI.
- [2025-11-24T19:03:51Z] `a181e74` Refactor PortableText component for improved grid tag filtering and code clarity: Simplify conditional checks in filterGridTags function, ensuring accurate text extraction. Update grid content processing to utilize original textBlock.children, fixing bugs related to grid filtering and enhancing overall functionality.
- [2025-11-25T03:02:20Z] `2305b5b` Add VoicesFromCommunitySection component and update CMSPage routing: Introduce a new VoicesFromCommunitySection to display community stories, enhancing user engagement. Update the CMSPage to conditionally render this section based on the slug, ensuring relevant content is presented for the care community page. Additionally, modify the App component to correct the slug path for the life-at-ccc route.
- [2025-11-25T03:48:31Z] `135ba21` Add InfoCard and InfoCards components, update PortableText for card support: Introduce InfoCard and InfoCards components to display structured information with dynamic styling. Enhance PortableText component to parse and render card content from custom tags, improving content presentation and user engagement. Add new images for visual enhancement.
- [2025-11-25T03:56:55Z] `7ecd1ae` Update README and refactor routing for community and services sections: Modify README to reflect new community members programme path. Remove Waitlist component and update routing in App and Community components to direct users to the new community members programme page. Adjust ServicesSection to link to the care and attention home instead of the waitlist. Enhance KeyHighlightsSection layout by reducing grid columns.
- [2025-11-25T04:54:52Z] `4e24950` Update README, add FAQ page, and refactor Events routing: Modify README to reflect new activities and events path. Introduce FAQ page with dynamic content fetching and SEO enhancements. Refactor Events component to align with updated routing for activities and events, ensuring accurate data retrieval and canonical URLs.
- [2025-11-25T05:12:05Z] `1ae07de` Add Blog link to Navigation and update SanityUpdate type: Introduce a new "Blog" navigation item for improved content access. Update SanityUpdate interface to allow a specific UpdateType for the 'type' field, enhancing type safety and flexibility.
- [2025-11-25T07:12:20Z] `faaf854` Update routing for Reports and Annual Reports sections: Modify links and data fetching paths in multiple components to reflect the new structure under "/who-we-are/publications/annual-reports". Enhance Updates component to accept a dynamic pageSlug prop for improved flexibility in content retrieval.
- [2025-11-25T08:11:46Z] `1fa3b16` Add Media and Press pages, update routing and components: Introduce MediaAndPress, GalleryDetail, and PressReleaseDetail pages for enhanced content organization. Update App routing to include new media and press routes, and add GalleryCard and PressReleaseCard components for displaying respective content. Refactor Updates component to support dynamic pageSlug and integrate case studies for improved content retrieval.
- [2025-11-25T08:20:31Z] `d7a50d7` Refactor PortableText component for enhanced card and grid processing: Update logic to handle filtered text for card and grid tags, ensuring accurate content extraction and rendering. Improve code clarity by consolidating conditional checks and enhancing the handling of dynamic content types in the Updates component.
- [2025-11-25T08:34:44Z] `3a299d7` Add dynamic sitemap generation and Sanity webhook integration: Create a new sitemap setup document detailing the dynamic sitemap process and automatic updates via Sanity webhooks. Implement sitemap generation logic in `api/sitemap.xml.ts`, configure Vercel rewrites and headers in `vercel.json`, and add webhook handling in `api/webhooks/sanity.ts`. Update `robots.txt` to include sitemap reference and enhance Sanity queries for comprehensive content fetching.
- [2025-11-25T08:39:21Z] `bf2dc86` Add TypeScript configuration for API: Create a new `tsconfig.api.json` file extending the base configuration to support API-specific TypeScript settings. Update the main `tsconfig.json` to include a reference to the new API configuration. Enhance Vite configuration for improved chunk splitting based on library types, optimizing the build process. Refactor Navigation component for better structure and readability, ensuring consistent rendering of navigation items. Update PortableText component to handle filtered text correctly for card content.
- [2025-11-25T08:44:47Z] `aa541da` Enhance Vite configuration for improved chunk splitting: Implement custom chunk file naming to ensure `react-vendor` loads first, and refine vendor chunk categorization for better dependency management. This update optimizes the build process by ensuring React-dependent libraries are prioritized and properly grouped, enhancing overall application performance.
- [2025-11-25T08:47:52Z] `3081ee9` Update import path for Sanity server client in sitemap generation: Change import statement in `api/sitemap.xml.ts` to use alias for improved module resolution and maintain consistency across the codebase.
- [2025-11-25T08:54:21Z] `8c5441f` Fix: Use relative import for sanity.server in API function
- [2025-11-25T08:55:25Z] `7d1b220` Refactor TypeScript configuration for API: Update `tsconfig.api.json` to include paths and compiler options, ensuring better module resolution. Add new `sanity.server.ts` file for Sanity client setup and adjust import paths in `sitemap.xml.ts` for consistency.
- [2025-11-25T08:57:32Z] `965c55b` Refactor TypeScript configuration and imports: Update `tsconfig.api.json` to change module resolution settings and disable importing TypeScript extensions. Simplify imports in `api/sitemap.xml.ts` by consolidating Sanity client exports into a new `api/lib/index.ts` file for improved organization and consistency.
- [2025-11-25T09:00:06Z] `1cb0b51` Update import paths for Sanity client in API: Change import statements in `api/sitemap.xml.ts` and `api/lib/index.ts` to include `.js` extensions for improved consistency and compatibility with module resolution.
- [2025-11-25T09:03:48Z] `8067265` Update canonical URLs and email addresses to reflect the new domain: Change all instances of "chinacoastcommunity.org" to "www.chinacoastcommunity.org.hk" across HTML, TypeScript, and configuration files for consistency and improved SEO.
- [2025-11-25T09:18:52Z] `4b94363` Refactor layout and improve responsiveness in AboutHero and Contact components: Remove unnecessary width classes and add min-width properties for better layout control. Update button text in GovernanceTeamSection for improved clarity on mobile and desktop views.
- [2025-12-02T12:49:53Z] `7e8e965` Refactor components and styles for consistency: Remove unused font-face declarations in CSS, update font-family in body and headings for improved typography, and standardize spelling of "centre" in Contact and "optimise" in Privacy components. Add spacing in various TypeScript files for better readability.
- [2025-12-03T08:08:42Z] `cdf9838` Add spacing for improved readability in multiple TypeScript files
- [2025-12-03T08:15:49Z] `ed8a8c7` Revise content in Future component for clarity and focus: Update text to emphasize the building's connection to nature and the new aromatic sensory garden. Adjust quotes for improved flow and relocate former residents' status for better readability.
- [2025-12-03T08:17:45Z] `e2bfa05` Update text in Future component for clarity: Modify wording to enhance the expression of community connections and improve overall readability.
- [2025-12-03T08:33:41Z] `231745d` Refactor CommitteeSection and TeamMembersSection components: Remove unused helper functions for initials and color generation, and streamline image rendering logic for team members. This cleanup enhances code readability and maintainability.
- [2025-12-03T08:46:56Z] `a5f2fc0` Refactor HeroSection component for improved readability: Reorganize import statements and enhance text clarity for better user understanding. Adjust formatting for consistency in JSX elements.
- [2025-12-03T08:58:49Z] `548dea5` Update RedevelopmentBanner component text for clarity: Modify wording to better convey the redevelopment status and enhance user understanding. Adjust formatting for improved readability in JSX structure.
- [2025-12-04T03:25:40Z] `dc7973d` Refactor Contact component: Update address details and remove unused sections for clarity. Enhance map display with an embedded Google Maps iframe for better user experience.
- [2025-12-04T03:36:05Z] `a923b4f` Update map location in Contact component: Change Google Maps iframe source and link to reflect the correct address for China Coast Community Ltd, enhancing user navigation.
- [2025-12-05T03:52:41Z] `ad0d580` chore: upgrade react and react-dom to version 19.2.1, update type definitions for react and react-dom
- [2025-12-10T03:44:41Z] `3c46faa` deps: update next-themes, react-day-picker, and vaul dependencies.
- [2025-12-25T10:21:18Z] `bf44923` chore: add spacing in multiple components for improved readability
- [2025-12-25T10:28:38Z] `7bf8276` fix: reorder "Care & Attention Home" in navigation items for better organization
- [2025-12-25T10:40:38Z] `d2a0239` refactor: remove unused sections from Index page and clean up KeyHighlightsSection
- [2025-12-25T11:51:58Z] `d0ca424` feat: add new hero images for improved visual appeal in HeroSection
- [2025-12-26T01:54:37Z] `bb3a47d` feat: implement PayBoxCards component and integrate into PortableText for donation page
- [2026-01-12T15:19:28Z] `ea1335b` refactor: simplify GovernanceTeamSection and BoardGovernance componen… (#2)
- [2026-01-14T17:02:26Z] `9b12f48` refactor: move committee members popup, change primary colour, update content (#3)
- [2026-01-20T11:03:01Z] `da26e81` Refactor (#4)
- [2026-01-21T16:51:42Z] `33d4a3c` Feature major donors (#5)
- [2026-01-22T09:40:16Z] `cb7a129` Feature/resend email integration (#6)
- [2026-01-24T04:29:07Z] `c8945c6` Add comprehensive logging for email sending flow (#7)
- [2026-01-26T12:00:47Z] `a0aa98f` Feature/email debug logging (#8)

### Commits ((none)) - oldest -> newest
- (branch not present)

### PRs - oldest -> newest (by created date)
- [2026-01-12T10:53:48Z] #1 [CLOSED] Update content on redevelopment page and move committee section to board-governance page (`main` <- `chore/redevelopment-content-changes`) | closed: 2026-01-12T11:55:58Z | https://github.com/invaritech-ai/CCC-redesign/pull/1
- [2026-01-12T12:11:34Z] #2 [MERGED] refactor: simplify GovernanceTeamSection and BoardGovernance componen… (`main` <- `refactor/redevelopment-page`) | merged: 2026-01-12T15:19:29Z | https://github.com/invaritech-ai/CCC-redesign/pull/2
- [2026-01-14T08:17:50Z] #3 [MERGED] refactor: move committee members popup, change primary colour, update content (`main` <- `refactor`) | merged: 2026-01-14T17:02:26Z | https://github.com/invaritech-ai/CCC-redesign/pull/3
- [2026-01-20T10:49:34Z] #4 [MERGED] Refactor (`main` <- `refactor`) | merged: 2026-01-20T11:03:02Z | https://github.com/invaritech-ai/CCC-redesign/pull/4
- [2026-01-21T16:28:59Z] #5 [MERGED] Feature major donors (`main` <- `feature-major-donors`) | merged: 2026-01-21T16:51:43Z | https://github.com/invaritech-ai/CCC-redesign/pull/5
- [2026-01-21T17:19:12Z] #6 [MERGED] Feature/resend email integration (`main` <- `feature/resend-email-integration`) | merged: 2026-01-22T09:40:17Z | https://github.com/invaritech-ai/CCC-redesign/pull/6
- [2026-01-22T10:10:59Z] #7 [MERGED] Add comprehensive logging for email sending flow (`main` <- `feature/email-debug-logging`) | merged: 2026-01-24T04:29:07Z | https://github.com/invaritech-ai/CCC-redesign/pull/7
- [2026-01-26T06:20:41Z] #8 [MERGED] Feature/email debug logging (`main` <- `feature/email-debug-logging`) | merged: 2026-01-26T12:00:48Z | https://github.com/invaritech-ai/CCC-redesign/pull/8

## EUDR
- default branch: master
- dev branch: (none)

### Commits (master) - oldest -> newest
- [2025-08-25T11:58:27Z] `baa352d` Add initial project structure with EUDR API client implementation
- [2025-09-02T01:34:08Z] `bf2dce0` Implement EUDR DDS submission functionality and enhance project structure
- [2025-09-02T02:12:33Z] `ff3df69` Update .gitignore to include additional documentation and configuration directories
- [2025-09-08T05:36:51Z] `906d7ee` feat: Enhance EUDR API client with V2 submission support and GeoJSON utilities
- [2025-09-08T06:17:59Z] `8463990` feat: Update documentation and tests for CF2 DDS submission success and supplementary unit scenarios
- [2025-09-08T07:00:09Z] `eeee213` feat: Add comprehensive guide for EUDR CF2 DDS submission parameters
- [2025-09-08T08:12:36Z] `2f3b1b0` feat: Implement DDS retrieval functionality and update documentation
- [2025-09-08T09:54:13Z] `02d7df7` feat: Implement CF4 error handling and update API client for enhanced error management
- [2025-09-08T14:41:32Z] `6cf94ba` Add integration tests for CF2-CF4 error handling
- [2025-09-09T15:32:10Z] `9986f1e` Refactor CF2-CF4 Error Integration Tests: Load test cases from JSON, enhance error handling, and implement index-based testing. Added CLI options for listing test cases and specifying test ranges. Improved logging and error reporting for better debugging.
- [2025-09-09T16:28:24Z] `863ba63` refactor: Remove XML debugging code and related files for cleaner integration tests
- [2025-09-09T16:40:30Z] `4cad0cd` refactor: Update test cases in JSON for improved structure and error handling
- [2025-09-12T12:17:16Z] `0ffd5a9` feat: Introduce EUDR FastAPI application with comprehensive DDS operations
- [2025-09-17T02:58:23Z] `3bf6225` feat: Add Geo Data Conversion Tool and API endpoints for EUDR compliance
- [2025-09-17T07:29:42Z] `cbf6db4` feat: Enhance echo service with SOAP response parsing and structured output
- [2025-09-19T02:38:39Z] `c77430c` feat: Enhance EUDR API with multiple operators support and logging improvements
- [2025-09-19T02:55:46Z] `cb01ed8` feat: Implement Representative EXPORT V1 and V2 endpoints with comprehensive testing
- [2025-09-19T04:39:27Z] `457a6ee` feat: Add AssociatedStatement model and integrate into DDS submission
- [2025-09-19T05:34:11Z] `a6e2374` feat: Add comprehensive EUDR Amendment Endpoint Guide and enhance amendment service error handling
- [2025-09-19T05:57:42Z] `af333b7` feat: Add debugging scripts for EUDR amendment process and enhance error handling
- [2025-09-19T06:11:31Z] `4fc4cc2` feat: Add debugging scripts for EUDR amendment process and enhance amendment service
- [2025-09-19T06:27:09Z] `f01d775` feat: Add testing scripts for FastAPI amendment endpoint with various scenarios
- [2025-09-19T06:44:09Z] `c5f7fa2` refactor: Update DDS amendment endpoint documentation for clarity and structure
- [2025-09-28T07:17:05Z] `7b94fae` feat: Introduce comprehensive amendment transformation and new retract endpoints
- [2025-09-28T07:33:21Z] `c3ee62a` feat: Add CF7 retrieval endpoints and testing framework for DDS operations
- [2025-09-28T11:12:24Z] `d574342` chore: Remove outdated documentation and scripts related to EUDR API
- [2025-09-28T15:29:05Z] `8f40518` feat: Enhance EUDR API with V2 support and comprehensive retrieval capabilities
- [2025-09-28T15:47:36Z] `136819e` refactor: Simplify amendment endpoint tags for clarity
- [2025-09-29T14:30:07Z] `800478b` feat: Refactor EUDR amendment client and enhance API endpoint structure
- [2025-09-29T14:32:35Z] `c755abc` chore: Remove obsolete testing scripts for EUDR API
- [2025-09-29T16:18:18Z] `72c61e4` chore: Update project name and enhance configuration in main application
- [2025-09-29T16:21:46Z] `6b811f1` Add Procfile for Sevalla deployment
- [2025-09-29T16:23:16Z] `9d22e85` Fix Procfile encoding issue
- [2025-09-29T16:26:40Z] `2ed3d2a` Fix ALLOWED_HOSTS for Sevalla deployment
- [2025-10-07T05:30:38Z] `e0ed60d` chore: Update .gitignore and refactor error handling in cf4_error_handling.py
- [2025-10-07T07:32:57Z] `c6a9f57` feat: Enhance error handling and response formatting in submission endpoints
- [2025-10-07T07:53:59Z] `0c124ac` chore: Update asyncio event loop policy for Windows compatibility
- [2025-10-07T08:02:59Z] `c98e225` feat: Implement Windows compatibility enhancements for FastAPI application
- [2025-10-07T08:03:11Z] `b25f7f4` chore: Add noqa comment to import statement in run.py
- [2025-10-16T08:26:19Z] `9a0064e` refactor: Clean up whitespace in main.py and retrieval.py
- [2025-10-17T07:35:56Z] `4086fbf` chore: Update .gitignore and enhance logging in EUDR API client
- [2025-10-17T08:35:18Z] `7bff6c7` refactor: Update amendment and CF7 retrieval endpoints for improved structure and functionality
- [2025-10-29T02:27:44Z] `c368888` feat: Add example environment configuration and enhance README for clarity
- [2025-10-29T02:28:28Z] `89533ba` fix: Update environment file references in README and WINDOWS_SETUP

### Commits ((none)) - oldest -> newest
- (branch not present)

### PRs - oldest -> newest (by created date)
- (no PRs)

## GymFlow-movement
- default branch: master
- dev branch: (none)

### Commits (master) - oldest -> newest
- [2025-04-25T04:19:37Z] `8ab0f8d` refactor: rename girth measurement fields for consistency across BMC records
- [2025-04-25T05:46:41Z] `4626785` Merge pull request #3 from Enclave-Technologies/v2-BMC
- [2025-04-25T10:36:47Z] `e444a39` refactor: improve code readability and structure in SettingsClient component
- [2025-04-26T00:00:36Z] `cb5dab1` Goals List and workout plan
- [2025-04-26T00:35:22Z] `0775e6e` Drag and drop for Exercises
- [2025-04-26T00:38:55Z] `7a22472` Code rabbit suggestion fix
- [2025-04-26T09:51:57Z] `0e15f36` feat: Implement user settings management with profile and password settings
- [2025-04-26T10:13:52Z] `d0e0aa1` feat: Enhance user settings form with automatic reset on user data change and improve image upload handling
- [2025-04-26T10:15:18Z] `b7b1d9f` feat: Refactor settings page by removing SettingsClient and consolidating user data handling in SettingsPage
- [2025-04-26T11:17:57Z] `cf66c6f` feat: Enforce access control by requiring Trainer or Admin role in various action functions
- [2025-04-26T11:40:52Z] `110c282` feat: Update user role handling to support multiple roles and improve guest approval checks
- [2025-04-26T12:17:56Z] `8fcdef4` feat: Add sharp library for image processing and convert uploaded images to WebP format
- [2025-04-26T12:42:07Z] `d4e4840` feat: Update Users table schema to allow nullable appwrite_id and add has_auth column
- [2025-04-26T16:15:38Z] `a9a34f1` feat: add client and trainer onboarding pages with forms
- [2025-04-26T16:51:50Z] `e3cf7bd` feat: add month and year dropdown selectors to calendar component
- [2025-04-26T18:22:02Z] `946b310` Refactor client and trainer registration logic
- [2025-04-27T04:26:55Z] `e8348b4` feat: enhance client creation process by adding trainer selection for existing users and updating user roles
- [2025-04-27T04:34:12Z] `1e1e4fc` feat: update navigation titles and routes for adding clients and trainers
- [2025-04-27T10:20:36Z] `ef832ce` chore: add @types/uuid and uuid dependencies to package.json
- [2025-04-27T17:38:14Z] `4ca7e58` feat(migrations): add updated_at column to ExercisePlans for concurrency control
- [2025-04-27T18:51:52Z] `a388972` feat: enhance workout plan functionality with additional exercise properties
- [2025-04-27T18:52:00Z] `c830c09` feat: enhance workout plan and exercise handling with additional fields for frontend compatibility
- [2025-04-27T19:09:23Z] `984f9c2` feat: implement deletion logic for removed phases, sessions, and exercises in workout plan updates
- [2025-04-27T19:51:11Z] `3cc8191` Remove unique constraint on Sessions table and update migration files
- [2025-04-27T20:00:04Z] `c150c61` feat: implement deep copy functionality for phases and sessions in workout planner
- [2025-04-27T20:20:39Z] `e932b83` feat: enhance goal management with CRUD operations and loading states
- [2025-04-27T21:21:30Z] `1ec8c1d` Refactor RecordWorkoutPage to enhance workout session management and local storage handling
- [2025-04-27T21:22:59Z] `50809ba` Merge pull request #4 from Enclave-Technologies/workout-goals
- [2025-04-27T21:30:34Z] `e3ef120` Merge branch 'master' into v2-ops-25Apr
- [2025-04-27T21:31:46Z] `0f5f3c1` Merge pull request #5 from Enclave-Technologies/v2-ops-25Apr
- [2025-04-27T21:36:11Z] `8db5429` refactor: update ExerciseDialog and WorkoutPlanner to improve type handling and remove unused props
- [2025-04-27T21:42:26Z] `969c376` refactor: update import paths for types in workout planning components
- [2025-04-28T03:43:14Z] `a4867f5` feat: add GlobalSearch component for client search functionality and improve workout volume calculation
- [2025-04-28T07:07:11Z] `0b27da9` feat: add BMCMeasurements, ExercisePlanExercises, ExercisePlans, Exercises, Goals, Phases, Roles, Sessions, TrainerClients, UserRoles, Users, WorkoutSessionDetails, and WorkoutSessionsLog tables with respective fields and constraints
- [2025-04-28T12:55:25Z] `c148176` Merge pull request #6 from Enclave-Technologies/global_search_edit_fixes
- [2025-04-28T16:06:42Z] `fd3fad8` UI for Add/Edit Exercise
- [2025-04-29T05:24:36Z] `e3b1d59` feat: implement safeImageUrl utility function and update image handling in various components
- [2025-04-29T09:07:02Z] `0b7f82b` refactor: remove console logs from getWorkoutPlanByClientId function
- [2025-04-30T04:46:31Z] `1bb9f40` refactor: update exercise properties in getAllExercises and related components for consistency
- [2025-04-30T05:16:34Z] `ab64efe` feat: enhance exercise filtering and sorting in getAllExercises function; update column definitions for consistency
- [2025-04-30T07:39:13Z] `8aab1c4` feat: add new migration entries and enhance schema with indexes
- [2025-04-30T08:05:02Z] `5c37dfd` Backend implementation
- [2025-04-30T08:06:22Z] `5d47e48` Merge remote-tracking branch 'origin/master' into add-edit-exercise
- [2025-04-30T14:42:29Z] `b7b2b94` Merge pull request #7 from Enclave-Technologies/add-edit-exercise
- [2025-04-30T15:14:50Z] `f2bb08a` Refactor RecordWorkoutPage into server and client components
- [2025-04-30T15:23:29Z] `87a4443` Merge branch 'master' into exercise-library-fix
- [2025-04-30T16:09:34Z] `8e6d6e3` Refactor exercise actions and columns for improved clarity and functionality
- [2025-04-30T16:10:46Z] `2c5d38a` Merge pull request #8 from Enclave-Technologies/exercise-library-fix
- [2025-04-30T16:16:59Z] `44e4419` Refactor ExercisePage for improved readability and parameter handling
- [2025-05-01T07:08:40Z] `fc53af6` Refactor query settings for immediate data refetching and update cache invalidation in AddExerciseForm
- [2025-05-01T15:35:19Z] `eb0459b` Add coach management functionality and improve client-coach switching logic
- [2025-05-01T16:53:40Z] `e182748` Refactor client-coach switching logic and enhance table integration for improved coach management
- [2025-05-01T17:06:55Z] `bcfb95f` Add refresh state management to TrainerCell and InfiniteTable for improved data handling
- [2025-05-01T17:12:30Z] `a0150f9` Add refresh state management to InfiniteTable and TrainerCell for improved data handling
- [2025-05-01T17:23:09Z] `f787791` Refactor staleTime settings across InfiniteTable components and QueryProvider for improved data freshness
- [2025-05-01T17:26:08Z] `b9bb262` Merge pull request #9 from Enclave-Technologies/fix_1May
- [2025-05-01T17:40:34Z] `7903ecc` Add RootLayout component and update LandingNav links for improved navigation
- [2025-05-05T05:00:22Z] `084c9cd` chore: add @types/lodash as a dev dependency
- [2025-05-05T05:15:40Z] `4a686e3` Refactor exercise sorting to use lexicographical ordering and improve readability in workout plan actions; enhance selected exercise handling in ExerciseTableInline component
- [2025-05-05T05:36:57Z] `131f083` Refactor cache invalidation logic to force complete revalidation of client pages; remove auto-save functionality and implement manual save tracking in WorkoutPlanner component
- [2025-05-06T04:56:39Z] `f07ff94` Edit trainer (#10)
- [2025-05-06T05:54:19Z] `b63d76d` Add CSV import/export functionality to WorkoutPlanner component; create WorkoutPlanCsvImportExport component and associated CSV handling utilities
- [2025-05-06T06:07:42Z] `468e8a9` Enhance CSV import/export functionality by adding exercises parameter to importWorkoutPlanFromCsv and updating WorkoutPlanCsvImportExport component to utilize it
- [2025-05-06T06:20:06Z] `97304ad` Refactor WorkoutPlanCsvImportExport component layout and update button styles for improved usability; replace anchor tag with Link for template download
- [2025-05-06T06:40:08Z] `bb7aae0` Add exercise export functionality to WorkoutPlanCsvImportExport component; implement downloadExercisesCsv and exportExercisesToCsv utilities
- [2025-05-06T08:30:39Z] `a2529df` Refactor InfiniteTable components to disable refetch on window focus and utilize keepPreviousData for improved data handling
- [2025-05-06T09:02:30Z] `7e8b333` Refactor filter application logic in CompactTableOperations to apply empty filter immediately when input is cleared; simplify filter condition check.
- [2025-05-06T09:10:50Z] `373e25c` Refactor ProfileSettings and SettingsForm components to improve unsaved changes alert; streamline layout and enhance user feedback.
- [2025-05-06T09:19:43Z] `0987222` Merge branch 'fixes_02May'
- [2025-05-06T15:11:24Z] `e552b3d` Refactor CompactTableOperations component for improved readability and functionality
- [2025-05-06T15:31:14Z] `fe613ea` Add router navigation for new exercise creation in InfiniteTable component
- [2025-05-06T15:59:02Z] `bef2d9d` Implement exercise approval status update functions and integrate with InfiniteTable component
- [2025-05-06T16:09:11Z] `46ca9f1` Refactor logout confirmation dialog text for clarity and consistency
- [2025-05-06T16:28:10Z] `2077030` Enhance ClientProfilePage layout and add notes section with edit functionality
- [2025-05-07T01:41:24Z] `d14091d` Refactor WorkoutPlanCsvImportExport component to use DropdownMenu for resource actions and improve UI organization
- [2025-05-07T01:54:25Z] `c3a1d0d` Optimize workout plan changes serialization for improved data handling
- [2025-05-07T02:03:56Z] `5c31919` Add validation for phaseId and sessionId in applyWorkoutPlanChanges and enhance logging for better debugging
- [2025-05-07T02:34:57Z] `4d94102` Enhance change serialization in WorkoutPlanner and add detailed logging for better debugging
- [2025-05-07T02:53:15Z] `595ddf1` Enhance applyWorkoutPlanChanges with detailed logging and validation for exerciseId; update addExercise to set default values for new exercises
- [2025-05-07T07:43:33Z] `3614a3f` Testing push ability
- [2025-05-07T20:39:50Z] `93f0ed9` Fixes 07 may pt2 (#13)
- [2025-05-08T07:53:35Z] `cd18ccb` Exercise flow (#15)
- [2025-05-08T10:25:34Z] `cd6e089` Fixes 08 may (#16)
- [2025-05-15T06:13:27Z] `d0212ba` Quickfix/workout plan (#17)
- [2025-05-15T15:44:11Z] `3e9480f` Fix/workout plan 15/05 (#18)
- [2025-05-16T06:26:02Z] `abd6ad6` Refactor unsaved changes detection in settings form to normalize values
- [2025-06-02T02:59:50Z] `65829bb` Preserving phase state after edits (#19)
- [2025-06-03T06:29:52Z] `ef6af0c` Preserving session state after edits (#20)
- [2025-06-11T08:15:09Z] `c4cd898` Feature: queue for workout planner (#23)
- [2025-06-11T09:05:43Z] `4034f4d` Display Client's Name During Workout Tracking (#21)
- [2025-06-11T09:21:45Z] `6dcec14` Display client's name during workout tracking (#24)
- [2025-06-11T09:51:53Z] `c854298` Export messageWorker with a more descriptive name for graceful shutdown
- [2025-06-11T10:32:10Z] `d353f0a` feat: Enhance workout tracking and session management
- [2025-06-11T10:55:18Z] `5820b4b` feat: Refactor workout-related types and database integration for worker processes
- [2025-06-11T11:54:37Z] `69b3bff` refactor: Update imports to use worker-schemas for database operations
- [2025-06-13T01:32:03Z] `4311686` Fixes 2.1 record workout (#25)
- [2025-06-30T08:39:57Z] `b05f0da` Fixes 2.2 (#26)
- [2025-08-06T02:51:21Z] `bbed3f6` Add Features for Workout Management
- [2025-08-21T00:07:29Z] `aeb927b` add ability to switch workouts, add ability to see history instances of the exercise
- [2025-08-25T11:52:06Z] `e937167` Deployment Test

### Commits ((none)) - oldest -> newest
- (branch not present)

### PRs - oldest -> newest (by created date)
- (no PRs)

## Support-portal-demo
- default branch: main
- dev branch: (none)

### Commits (main) - oldest -> newest
- [2026-01-25T05:30:21Z] `8efead8` Initialize repository; add root .gitignore for node_modules
- [2026-01-25T15:29:47Z] `5c21af9` 3-way chat
- [2026-01-26T05:16:57Z] `0603539` Dockerfile
- [2026-01-26T06:23:54Z] `fb0f5c6` Updated nginx and dockerfile
- [2026-01-26T06:28:24Z] `950cc20` Fixed the command npm install
- [2026-01-26T06:39:00Z] `eaa7628` Nginx fix
- [2026-01-26T06:41:36Z] `7419fc5` Fixing the npm run build issue
- [2026-01-26T07:29:12Z] `c5bb439` Fixed the issue with scroll and multiple customer
- [2026-01-26T10:44:24Z] `3dd859d` - Update server dependencies to include 'pg' and its types.
- [2026-01-26T10:49:22Z] `ad0402a` Update dashboard audio player and fix save button layout
- [2026-01-26T10:52:33Z] `3ab9f6d` Fix image and audio preview URLs in dashboard support chat
- [2026-01-26T11:43:59Z] `6f0f039` Merge pull request #1 from invaritech-ai/design/ui-cleanup
- [2026-01-26T13:36:27Z] `109a6f1` Implement authentication context and API integration for agent management
- [2026-01-26T13:48:32Z] `cc81d5e` Enhance authentication and testing framework
- [2026-01-26T14:26:59Z] `b8a17aa` - Improved layout and styling of various components across the dashboard, including agent and department lists.
- [2026-01-26T14:41:58Z] `aeffce2` Enhance team management functionality in DepartmentList component
- [2026-01-26T17:52:50Z] `a9fa420` Add ticket assignment and tagging features with TanStack Query integration
- [2026-01-26T18:10:19Z] `432aeaf` Adjust input field's text color for better visibility
- [2026-01-27T06:13:28Z] `1768f08` Implement message metadata and pagination support in ticketing system
- [2026-01-27T07:27:01Z] `8949527` Add audit trail functionality for ticketing system
- [2026-01-27T09:12:41Z] `7996864` Refactor message handling and enhance ticketing system features
- [2026-01-27T11:23:16Z] `4747694` Implement views management and enhance inbox functionality
- [2026-01-27T12:28:40Z] `178dbed` Refactor message scrolling behavior in dashboard and widget components
- [2026-01-27T15:41:40Z] `3c71703` Add macros functionality to the dashboard
- [2026-01-28T08:28:06Z] `d5b6628` Enhance MacrosPage functionality with agent context integration
- [2026-01-28T12:13:50Z] `ab2174a` Merge pull request #7 from invaritech-ai/feat/macros
- [2026-01-28T12:14:42Z] `2170541` Merge pull request #2 from invaritech-ai/feat/identity-roles
- [2026-01-28T13:16:13Z] `1cb213a` Merge pull request #6 from invaritech-ai/feat/ticket-views
- [2026-01-28T14:26:51Z] `892f6f3` Add audit logging functionality for message events
- [2026-01-29T04:35:00Z] `4261fca` Fix compose deploy: auto-migrate and reliable builds
- [2026-01-29T04:36:28Z] `f83cf11` Merge pull request #8 from invaritech-ai/hot-fix-29-01-2026
- [2026-01-29T05:35:28Z] `a80f515` Merge pull request #9 from invaritech-ai/feat/audit-trail
- [2026-01-29T06:55:13Z] `1575e07` Add Ticket Timeline component and useTicketAudit hook
- [2026-01-29T07:22:43Z] `ea56613` Refactor TicketTimeline and TicketPage components for improved event display
- [2026-01-29T09:21:15Z] `0fc304e` Add Developers Corner page and API endpoint documentation
- [2026-01-29T09:41:44Z] `78ebda7` Enhance Developers Corner with user management and log clearing functionality
- [2026-01-29T09:49:37Z] `ece6f2d` Refactor Developers Corner layout.
- [2026-01-29T13:19:49Z] `7444f28` Add TicketChat components, enhance TicketControls and TicketRightPanel
- [2026-01-29T14:12:18Z] `4298a8d` Enhance TicketControls component with additional state management and functionality
- [2026-01-29T15:26:39Z] `30220da` Enhance TicketChat component with internal note functionality
- [2026-01-30T07:40:31Z] `ce6478d` Implement macro message preview with variable substitution
- [2026-01-30T07:59:03Z] `248d41e` Implement a sticky top bar that displays relevant information.
- [2026-01-31T15:41:02Z] `83e3ab4` Merge pull request #10 from invaritech-ai/feat/ticket-timeline
- [2026-01-31T16:03:30Z] `24e3ce1` Merge pull request #11 from invaritech-ai/feat/dev-corner
- [2026-02-01T07:39:04Z] `981a8fc` Swagger UI
- [2026-02-01T07:41:31Z] `9d7ee70` Merge pull request #16 from invaritech-ai/feat/dev-corner
- [2026-02-01T07:55:43Z] `e231c25` Merge pull request #12 from invaritech-ai/update/support-chat-controls
- [2026-02-03T14:27:17Z] `1dc4efb` Fixing the issue with build

### Commits ((none)) - oldest -> newest
- (branch not present)

### PRs - oldest -> newest (by created date)
- [2026-01-26T11:32:21Z] #1 [MERGED] Design/UI cleanup (`main` <- `design/ui-cleanup`) | merged: 2026-01-26T11:43:59Z | https://github.com/invaritech-ai/Support-portal-demo/pull/1
- [2026-01-26T14:52:57Z] #2 [MERGED] Add identity, roles, departments, and teams. (`main` <- `feat/identity-roles`) | merged: 2026-01-28T12:14:42Z | https://github.com/invaritech-ai/Support-portal-demo/pull/2
- [2026-01-26T19:11:08Z] #3 [MERGED] Add ticket management (`feat/identity-roles` <- `feat/ticket-management`) | merged: 2026-01-28T12:15:24Z | https://github.com/invaritech-ai/Support-portal-demo/pull/3
- [2026-01-27T06:18:06Z] #4 [MERGED] Add message pagination and metadata (`feat/ticket-management` <- `feat/messaging`) | merged: 2026-01-28T13:15:07Z | https://github.com/invaritech-ai/Support-portal-demo/pull/4
- [2026-01-27T07:40:32Z] #5 [MERGED] Add audit trail functionality for ticketing system (`feat/messaging` <- `feat/audit-trail`) | merged: 2026-01-29T05:29:10Z | https://github.com/invaritech-ai/Support-portal-demo/pull/5
- [2026-01-27T16:05:13Z] #6 [MERGED] Add Ticket Views  (`feat/audit-trail` <- `feat/ticket-views`) | merged: 2026-01-28T13:16:13Z | https://github.com/invaritech-ai/Support-portal-demo/pull/6
- [2026-01-28T09:14:35Z] #7 [MERGED] Add macros functionality to the dashboard (`feat/ticket-views` <- `feat/macros`) | merged: 2026-01-28T12:13:50Z | https://github.com/invaritech-ai/Support-portal-demo/pull/7
- [2026-01-29T04:36:15Z] #8 [MERGED] Fix compose deploy: auto-migrate and reliable builds (`main` <- `hot-fix-29-01-2026`) | merged: 2026-01-29T04:36:28Z | https://github.com/invaritech-ai/Support-portal-demo/pull/8
- [2026-01-29T05:30:56Z] #9 [MERGED] Feat/audit trail (`main` <- `feat/audit-trail`) | merged: 2026-01-29T05:35:28Z | https://github.com/invaritech-ai/Support-portal-demo/pull/9
- [2026-01-29T07:36:45Z] #10 [MERGED] Ticket timeline (`main` <- `feat/ticket-timeline`) | merged: 2026-01-31T15:41:02Z | https://github.com/invaritech-ai/Support-portal-demo/pull/10
- [2026-01-29T10:13:51Z] #11 [MERGED] Add Dev Corner (`main` <- `feat/dev-corner`) | merged: 2026-01-31T16:03:30Z | https://github.com/invaritech-ai/Support-portal-demo/pull/11
- [2026-01-29T17:48:41Z] #12 [MERGED] Update Support Chat controls (`main` <- `update/support-chat-controls`) | merged: 2026-02-01T07:55:43Z | https://github.com/invaritech-ai/Support-portal-demo/pull/12
- [2026-01-30T10:12:28Z] #13 [OPEN] Implement custom fields (`main` <- `feat/custom-fields-implementation`) | https://github.com/invaritech-ai/Support-portal-demo/pull/13
- [2026-01-30T12:32:22Z] #14 [OPEN] Add multi-level categories (`main` <- `feat/multi-level-categories`) | https://github.com/invaritech-ai/Support-portal-demo/pull/14
- [2026-01-30T17:18:34Z] #15 [OPEN] Implement internal collaboration (`main` <- `feat/internal-collaboration`) | https://github.com/invaritech-ai/Support-portal-demo/pull/15
- [2026-02-01T07:40:28Z] #16 [MERGED] Swagger UI (`main` <- `feat/dev-corner`) | merged: 2026-02-01T07:41:31Z | https://github.com/invaritech-ai/Support-portal-demo/pull/16
- [2026-02-02T10:02:25Z] #17 [OPEN] Fix message read update status (`feat/internal-collaboration` <- `fix/message-read-status`) | https://github.com/invaritech-ai/Support-portal-demo/pull/17
- [2026-02-02T13:10:06Z] #18 [OPEN] Implement coderabbit suggestions (`fix/message-read-status` <- `fix/coderabbit-suggestions`) | https://github.com/invaritech-ai/Support-portal-demo/pull/18
- [2026-02-05T07:53:31Z] #19 [OPEN] Add Vector Search (`fix/coderabbit-suggestions` <- `feat/vector-search`) | https://github.com/invaritech-ai/Support-portal-demo/pull/19

## contact-form-API
- default branch: master
- dev branch: (none)

### Commits (master) - oldest -> newest
- [2025-09-16T12:48:18Z] `6d66966` Initialize Invaritech Contact API project with Next.js, including API routes for contact form submissions, CORS support, environment variable configuration, and a comprehensive README. Added .gitignore, TypeScript configuration, and Vercel deployment settings.
- [2025-09-16T13:28:52Z] `3cf19ac` Refactor contact API to implement reCAPTCHA validation and remove webhook secret handling. Update example environment file to reflect changes in configuration options.
- [2025-11-23T16:52:35Z] `d17dd3a` feat: added weekend form submission api
- [2025-12-05T04:34:28Z] `651b2e2` chore: update dependencies for next, react, and typescript packages
- [2025-12-07T09:42:39Z] `9f047fd` fix: update variable naming to prevent unused variable warnings in contact API

### Commits ((none)) - oldest -> newest
- (branch not present)

### PRs - oldest -> newest (by created date)
- [2026-01-16T05:13:00Z] #1 [OPEN] Fix React Server Components CVE vulnerabilities (`master` <- `vercel/react-server-components-cve-vu-uywrn3`) | https://github.com/invaritech-ai/contact-form-API/pull/1

## invaritech-website-next
- default branch: main
- dev branch: (none)

### Commits (main) - oldest -> newest
- [2025-09-15T14:41:24Z] `54279eb` Initial commit from Create Next App
- [2025-09-16T03:00:11Z] `d63b505` Add initial project structure with components, styles, and configuration
- [2025-09-16T04:19:14Z] `bd70b10` Enhance layout and SEO with new metadata, structured data, and manifest files
- [2025-09-16T04:26:51Z] `bea6d05` Refactor components for improved accessibility and consistency
- [2025-09-16T08:30:11Z] `4a30390` Implement deployment guides and reCAPTCHA integration for lead capture
- [2025-09-16T10:23:22Z] `d4e0be7` Implement contact form with validation and submission handling
- [2025-09-16T12:26:41Z] `32ddcb4` Update API configuration, enhance SEO documentation, and refactor components
- [2025-09-16T13:03:19Z] `ba40cbc` Merge pull request #1 from invaritech-ai/contact-form
- [2025-09-16T13:07:28Z] `64d79c8` Update GitHub Actions workflows to use pnpm for dependency management
- [2025-09-16T13:10:32Z] `21e043b` Refactor GitHub Actions workflow to streamline pnpm setup
- [2025-09-16T13:12:36Z] `fc3ae55` Disable automatic deployment in deploy.yml and retain manual trigger; streamline pnpm setup in hostinger-deploy.yml by removing duplicate step.
- [2025-09-16T13:29:17Z] `72be572` Update GitHub Actions workflow for Hostinger deployment and refine contact form component
- [2025-09-16T14:04:03Z] `fb20aaa` Refactor ContactSection and Footer components for improved content and structure
- [2025-09-16T14:24:09Z] `f0c2bca` Update HeroSection component styles and layout for improved responsiveness
- [2025-09-16T14:59:22Z] `ad457b6` Refactor Contact, Footer, Header, Integrations, and WhatWeDo components for improved structure and navigation
- [2025-09-16T15:15:01Z] `470bbca` Update HeroSection link to direct users to the contact section for improved navigation
- [2025-11-18T12:15:50Z] `6737204` Add analytics script to RootLayout for enhanced tracking
- [2025-11-23T03:52:27Z] `495b1c3` chore: Add Next.js framework, project dependencies, and local environment configuration.
- [2025-11-23T04:57:28Z] `4241f40` chore: install project dependencies and Next.js build artifacts.
- [2025-11-23T09:24:20Z] `3dbcc46` chore: Add Next.js and its associated dependencies.
- [2025-11-23T11:30:28Z] `769824c` chore: Install Next.js and other project dependencies.
- [2025-11-23T11:34:33Z] `7d97740` feat: Initialize project with installed dependencies and build artifacts.
- [2025-11-23T14:21:22Z] `df83d7f` chore: Install project dependencies by adding numerous files to node_modules.
- [2025-11-23T15:56:25Z] `55664b3` chore: updated sitemap, robots, llms, and fixed a few minor visual issues
- [2025-11-23T16:29:44Z] `68249df` chore: eudr images and links updated
- [2025-11-23T16:52:12Z] `04320f1` chore: updated weekend form link
- [2025-11-23T17:12:34Z] `f28e470` chores: metadata updates
- [2025-11-23T17:22:57Z] `29d7b95` chores: og image update and metadata update
- [2025-11-23T18:15:28Z] `472a7bf` chore: update metadata, optimize images, and improve performance with lazy loading for sections
- [2025-11-23T18:22:22Z] `62d5606` chore: update canonical URLs to include 'www' for consistency across all pages
- [2025-11-23T18:24:08Z] `35c70c6` chore: update URLs to include 'www' for consistency across all relevant pages
- [2025-11-23T18:29:49Z] `9cdfab6` chore: update URLs to 'www' version and refine structured data for improved SEO and clarity
- [2025-11-24T07:50:21Z] `38d5926` fix: standardize 'Weekend Suite' to 'WeekendSuite' and correct hyphenation in various sections for consistency
- [2025-11-26T10:08:26Z] `c65ef3a` fix: correct HTML entity for apostrophe in charity website description for proper rendering
- [2025-11-26T14:27:56Z] `bd6f3c2` added instantly pixel
- [2025-11-26T15:01:04Z] `cd93acb` chore: add trailing slashes to URLs in sitemap and metadata for consistency and improved SEO
- [2025-11-26T15:28:15Z] `d4cb654` chore: implement lazy loading for ContactSection, optimize font loading, and add trailing slashes to URLs for consistency
- [2025-11-26T15:39:45Z] `6b1c9ba` chore: update metadata to use WebP format for images across multiple pages and add trailing slashes to URLs for consistency
- [2025-11-27T00:15:30Z] `ef92710` chore: update contact links to point to Calendly for scheduling consultations and enhance button components for external links
- [2025-11-27T00:38:54Z] `05b8da1` refactor: enhance compliance bridge page content for clarity and engagement, update terminology, and add timeline and guarantee sections
- [2025-11-27T00:43:20Z] `dde7cc3` feat: add guarantee section to HowWeWork component, enhancing user assurance with clear commitment details
- [2025-11-27T00:57:42Z] `db25f8b` feat: create Blogs and Services pages with metadata and content for enhanced user engagement and SEO
- [2025-11-27T00:57:48Z] `287a12a` feat: add new service and blog URLs to sitemap with updated metadata for improved SEO
- [2025-11-27T01:10:48Z] `e96c5b8` feat: implement blog functionality with dynamic routing, metadata generation, and post listing for enhanced user experience and SEO
- [2025-11-27T01:23:02Z] `fab12b8` chore: update blog cover images to WebP format for improved performance and consistency
- [2025-11-27T01:31:28Z] `29a9b66` fix: replace apostrophes with HTML entities in text content across multiple pages for improved compatibility
- [2025-11-27T01:42:58Z] `f6455c2` fix: update URLs in sitemap and blog metadata to include trailing slashes for consistency and improved SEO
- [2025-11-27T01:54:28Z] `1d4199b` fix: ensure trailing slashes in blog URLs for consistency and SEO, and update .htaccess for non-www to www redirection
- [2025-11-27T02:06:51Z] `17ae619` fix: update URLs to include trailing slashes for consistency and SEO, and revise team member descriptions for clarity
- [2025-12-02T13:59:24Z] `616cbd3` feat: add new blog posts on compliance automation, EUDR challenges, and the benefits of automation for consultancies, along with associated cover images for improved user engagement and SEO
- [2025-12-02T14:21:10Z] `b097c3c` feat: update blog posts to reflect new titles and content on compliance automation, including the addition of new articles on consultancies and EUDR challenges, enhancing clarity and user engagement
- [2025-12-02T15:54:16Z] `4c73abe` feat: enhance page layouts and UI components across multiple pages, introducing a new PageLayout component for consistent styling, and updating badge styles for improved visibility and user experience
- [2025-12-02T17:16:52Z] `1f04b12` feat: add new blog posts on RegOps strategy, consultancy traps, and technical integration, enhancing content diversity and user engagement, along with associated cover images for improved visibility
- [2025-12-02T17:29:27Z] `0235d39` feat: enhance compliance bridge page with related resources section, adding links to case studies and technical deep dives for improved user engagement and navigation
- [2025-12-02T17:35:31Z] `37b17bb` feat: update compliance bridge and work pages with new blog links and enhanced project descriptions for better user navigation and engagement
- [2025-12-02T17:40:14Z] `54927fe` fix: standardize blog post URLs by adding trailing slashes across multiple files to ensure consistency and improve SEO
- [2025-12-02T18:10:33Z] `91a5eb9` feat: add custom 404 page with engaging visuals and navigation, including light and dark mode images for improved user experience
- [2025-12-02T18:17:00Z] `0800264` fix: standardize apostrophe usage in text across multiple pages for improved readability and consistency
- [2025-12-02T18:34:38Z] `4c17edf` fix: standardize text formatting and URL structures across multiple blog posts for improved readability and consistency
- [2025-12-05T04:32:00Z] `f3b3f9c` Refactor code structure for improved readability and maintainability
- [2025-12-07T09:42:21Z] `9aa0d39` feat: add custom fonts and update layout for improved typography and user experience
- [2025-12-07T09:53:22Z] `bf488fa` fix: update logo image URLs in structured data for consistency
- [2025-12-07T09:53:45Z] `f13caae` fix: update logo image URL in structured data for accuracy
- [2025-12-07T10:34:00Z] `33899f6` fix: update social media links and site references for consistency and accuracy
- [2025-12-07T11:06:43Z] `fc8f1e7` fix: update founding date in structured data to reflect the correct year
- [2025-12-08T17:53:13Z] `3c0d68e` feat: add new file with unique identifier for tracking purposes on IndexNow
- [2025-12-24T04:58:05Z] `41a471e` refactor: enhance job description for Full-Stack Developer role, improving clarity and engagement; update text for consistency and readability
- [2025-12-30T11:21:29Z] `156b2e1` feat: integrate chatbot component into layout and add chatbot functionality using flowise-embed-react; include new image asset for visual enhancement
- [2025-12-30T11:24:24Z] `61688a7` feat: dynamically import BubbleChat component to optimize client-side rendering in chatbot
- [2025-12-30T11:47:11Z] `551c033` feat: add Terms and Conditions page with detailed sections on user agreement, chatbot usage, and liability; enhance chatbot component with new configuration options and disclaimer
- [2025-12-30T11:50:24Z] `c910710` fix: update HTML entities in TermsPage for proper rendering of special characters
- [2025-12-30T12:18:11Z] `43a512f` feat: enhance chatbot configuration by adding custom CSS to hide specific elements and updating source documents display settings
- [2025-12-30T12:24:35Z] `0a73127` feat: update chatbot configuration to include returnSourceDocuments option and enhance CSS for improved element visibility control
- [2025-12-30T12:36:52Z] `3ca03dd` feat: implement keyboard shortcut to trigger chat button click and enhance chat button detection methods in Chatbot component
- [2026-01-20T13:28:37Z] `811ff49` Immediate edits (#2)
- [2026-02-11T14:55:13Z] `482fea8` Refresh About, Careers, and Contact pages (#3)

### Commits ((none)) - oldest -> newest
- (branch not present)

### PRs - oldest -> newest (by created date)
- [2025-09-16T13:02:26Z] #1 [MERGED] Contact form (`main` <- `contact-form`) | merged: 2025-09-16T13:03:19Z | https://github.com/invaritech-ai/invaritech-website-next/pull/1
- [2026-01-20T13:18:27Z] #2 [MERGED] Immediate edits (`main` <- `immediate-edits`) | merged: 2026-01-20T13:28:37Z | https://github.com/invaritech-ai/invaritech-website-next/pull/2
- [2026-02-11T12:43:55Z] #3 [MERGED] Refresh About, Careers, and Contact pages (`main` <- `feat/about-careers-contact-refresh`) | merged: 2026-02-11T14:55:13Z | https://github.com/invaritech-ai/invaritech-website-next/pull/3

## physio-whatsapp-flow
- default branch: main
- dev branch: dev

### Commits (main) - oldest -> newest
- [2026-01-13T03:42:57Z] `a200bf9` First Commit
- [2026-01-13T17:02:39Z] `fd59866` feat:Add payment flow handling for physio sessions
- [2026-01-15T11:10:41Z] `b68914e` feat: Implement physio session notes feature and debug mode for manual role switching
- [2026-01-29T06:06:52Z] `300d04e` Merge pull request #1 from invaritech-ai/feat/extend-physio-flow
- [2026-01-29T07:55:30Z] `1b1030b` Merge pull request #2 from invaritech-ai/feat/add-physio-notes
- [2026-01-29T07:56:28Z] `5f2a564` Merge pull request #3 from invaritech-ai/feat/extend-physio-flow
- [2026-02-06T02:26:38Z] `8bc160f` Update issue templates

### Commits (dev) - oldest -> newest
- [2026-01-13T03:42:57Z] `a200bf9` First Commit
- [2026-01-13T17:02:39Z] `fd59866` feat:Add payment flow handling for physio sessions
- [2026-01-15T11:10:41Z] `b68914e` feat: Implement physio session notes feature and debug mode for manual role switching
- [2026-01-29T06:06:52Z] `300d04e` Merge pull request #1 from invaritech-ai/feat/extend-physio-flow
- [2026-01-29T07:55:30Z] `1b1030b` Merge pull request #2 from invaritech-ai/feat/add-physio-notes
- [2026-01-29T07:56:28Z] `5f2a564` Merge pull request #3 from invaritech-ai/feat/extend-physio-flow
- [2026-02-04T11:48:08Z] `bb4d4e7` Feat repo structure (#4)
- [2026-02-06T02:58:25Z] `158675f` Feat/user auth (#6)
- [2026-02-06T14:47:25Z] `483cd0a` feat: integrate Celery with Redis for async task processing (#15)
- [2026-02-06T15:04:36Z] `11d8de9` fix: critical audit fixes — broken Calendly params, traceback leaks, stale debug code (#19)
- [2026-02-06T16:19:44Z] `e2cc566` Phase 1: Data Model Foundation + Clean Slate DB (#26)
- [2026-02-07T04:05:18Z] `e8f69b7` feat: Phase 2 - WhatsApp Bot Rewrite (Customer IVR) (#27)
- [2026-02-07T04:11:41Z] `b891c39` chore: bounding matched_therapists to be None if not available
- [2026-02-07T05:02:08Z] `a68e369` feat: add Docker support with Dockerfile and docker-compose
- [2026-02-08T07:01:25Z] `f749f08` feat: main menu, global keywords, and admin APIs (#28)
- [2026-02-10T10:31:19Z] `187ac4e` feat: security hardening + free-form specialty creation (#29)
- [2026-02-11T08:36:37Z] `2afc80f` feat(auth): admin user management, onboarding, session APIs, and revocation fix (#32)

### PRs - oldest -> newest (by created date)
- [2026-01-13T17:05:34Z] #1 [MERGED] feat:Add payment flow handling for physio sessions (`main` <- `feat/extend-physio-flow`) | merged: 2026-01-29T06:06:52Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/1
- [2026-01-15T11:43:05Z] #2 [MERGED] feat: Implement physio session notes feature (`feat/extend-physio-flow` <- `feat/add-physio-notes`) | merged: 2026-01-29T07:55:30Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/2
- [2026-01-29T07:56:18Z] #3 [MERGED] Feat/extend physio flow (`main` <- `feat/extend-physio-flow`) | merged: 2026-01-29T07:56:28Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/3
- [2026-02-04T11:44:52Z] #4 [MERGED] Feat repo structure (`dev` <- `feat-repo-structure`) | merged: 2026-02-04T11:48:08Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/4
- [2026-02-06T02:54:28Z] #6 [MERGED] Feat/user auth (`dev` <- `feat/user-auth`) | merged: 2026-02-06T02:58:25Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/6
- [2026-02-06T06:26:09Z] #15 [MERGED] feat: integrate Celery with Redis for async task processing (`dev` <- `feat/add-celery-redis-queue`) | merged: 2026-02-06T14:47:25Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/15
- [2026-02-06T08:35:32Z] #16 [MERGED] feat: implement WhatsApp message processing with Celery (`feat/add-celery-redis-queue` <- `feat/add-manual-endpoint-testing`) | merged: 2026-02-06T14:45:15Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/16
- [2026-02-06T09:39:09Z] #17 [MERGED] fix: add missing greeting response to customer messages in bot logic (`feat/add-manual-endpoint-testing` <- `fix/add-missing-greeting-handler`) | merged: 2026-02-06T14:42:43Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/17
- [2026-02-06T11:04:18Z] #18 [MERGED] feat: add session notes API and update session_note model (`fix/add-missing-greeting-handler` <- `feat/create-session-notes-api`) | merged: 2026-02-06T14:31:57Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/18
- [2026-02-06T15:01:23Z] #19 [MERGED] fix: critical audit fixes (`dev` <- `fix/critical-audit-fixes`) | merged: 2026-02-06T15:04:36Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/19
- [2026-02-06T16:19:07Z] #26 [MERGED] Phase 1: Data Model Foundation + Clean Slate DB (`dev` <- `v1/phase-1-data-models`) | merged: 2026-02-06T16:19:44Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/26
- [2026-02-06T17:33:50Z] #27 [MERGED] feat: Phase 2 - WhatsApp Bot Rewrite (Customer IVR) (`dev` <- `v1/phase-2-whatsapp-bot`) | merged: 2026-02-07T04:05:18Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/27
- [2026-02-08T05:34:08Z] #28 [MERGED] feat: main menu, global keywords, and admin APIs (`dev` <- `v1/phase-2.5-admin-apis`) | merged: 2026-02-08T07:01:26Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/28
- [2026-02-10T10:04:12Z] #29 [MERGED] feat: security hardening + free-form specialty creation (`dev` <- `v1/phase-3-matching`) | merged: 2026-02-10T10:31:19Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/29
- [2026-02-11T08:25:03Z] #32 [MERGED] feat(auth): admin user management, onboarding, session APIs, and revocation fix (`dev` <- `v1/phase-3-matching`) | merged: 2026-02-11T08:36:38Z | https://github.com/invaritech-ai/physio-whatsapp-flow/pull/32

## physio-whatsapp-frontend
- default branch: main
- dev branch: dev

### Commits (main) - oldest -> newest
- [2026-02-05T14:15:27Z] `fdb829b` chore: initial project scaffolding
- [2026-02-05T14:15:51Z] `9889a9a` feat: add app source
- [2026-02-05T14:16:04Z] `9d59147` docs: add README
- [2026-02-05T15:19:22Z] `5c0cfbd` chore: update me endpoint
- [2026-02-09T05:20:58Z] `1f3d203` feat: add developer requirements document for Physio WhatsApp frontend
- [2026-02-09T10:05:00Z] `17b9216` Add UX journey diagram for user flows
- [2026-02-09T11:16:41Z] `fa96faf` refactor: update UX journey diagram to include therapist onboarding steps and approved email whitelist

### Commits (dev) - oldest -> newest
- [2026-02-05T14:15:27Z] `fdb829b` chore: initial project scaffolding
- [2026-02-05T14:15:51Z] `9889a9a` feat: add app source
- [2026-02-05T14:16:04Z] `9d59147` docs: add README
- [2026-02-05T15:19:22Z] `5c0cfbd` chore: update me endpoint
- [2026-02-09T05:20:58Z] `1f3d203` feat: add developer requirements document for Physio WhatsApp frontend
- [2026-02-09T10:05:00Z] `17b9216` Add UX journey diagram for user flows
- [2026-02-09T11:16:41Z] `fa96faf` refactor: update UX journey diagram to include therapist onboarding steps and approved email whitelist
- [2026-02-10T10:14:32Z] `647089b` feat: foundation pages, auth routing, and branding shell (#14)
- [2026-02-10T10:40:15Z] `82c0b8f` fix: polyfill crypto.randomUUID for non-secure contexts
- [2026-02-10T14:32:09Z] `93f7ab2` sync: authz routing + admin access management progress (#15)
- [2026-02-10T17:21:29Z] `3ab465e` Therapist onboarding wizard, auth route hardening, and admin filter fix (#16)
- [2026-02-11T02:54:49Z] `6f4e696` feat: build therapist calendar dashboard with sessions API integration (#17)
- [2026-02-11T08:33:50Z] `e506f19` Admin management pages, specialties CRUD, and mobile nav fix (#18)

### PRs - oldest -> newest (by created date)
- [2026-02-10T09:56:46Z] #14 [MERGED] feat: foundation pages, auth routing, and branding shell (`dev` <- `feat-2-foundation-shared-pages`) | merged: 2026-02-10T10:14:33Z | https://github.com/invaritech-ai/physio-whatsapp-frontend/pull/14
- [2026-02-10T13:35:51Z] #15 [MERGED] sync: authz routing + admin access management progress (`dev` <- `feat/authz-routing-admin-access-ux`) | merged: 2026-02-10T14:32:09Z | https://github.com/invaritech-ai/physio-whatsapp-frontend/pull/15
- [2026-02-10T17:05:51Z] #16 [MERGED] Therapist onboarding wizard, auth route hardening, and admin filter fix (`dev` <- `feat/therapist-onboarding-wizard-mvp`) | merged: 2026-02-10T17:21:29Z | https://github.com/invaritech-ai/physio-whatsapp-frontend/pull/16
- [2026-02-11T02:53:13Z] #17 [MERGED] feat: therapist calendar dashboard with sessions APIs (`dev` <- `feat/therapist-dashboard-calendar`) | merged: 2026-02-11T02:54:49Z | https://github.com/invaritech-ai/physio-whatsapp-frontend/pull/17
- [2026-02-11T03:00:41Z] #18 [MERGED] Admin management pages, specialties CRUD, and mobile nav fix (`dev` <- `feat/admin-management-pages-mvp`) | merged: 2026-02-11T08:33:50Z | https://github.com/invaritech-ai/physio-whatsapp-frontend/pull/18
- [2026-02-11T08:15:13Z] #20 [CLOSED] Fix session revocation loop & add therapist workspace layout (`main` <- `feat/admin-management-pages-mvp`) | closed: 2026-02-11T08:22:58Z | https://github.com/invaritech-ai/physio-whatsapp-frontend/pull/20

## resto-pilot-backend
- default branch: main
- dev branch: develop

### Commits (main) - oldest -> newest
- [2025-12-10T03:33:54Z] `4eac2f4` Initialize project structure with .gitignore, .python-version, README.md, main.py, and pyproject.toml
- [2025-12-10T11:02:07Z] `2d24fcc` PoC - merge 1 (#1)
- [2025-12-10T11:07:30Z] `4f7f9ef` Update Lambda deployment workflow to install dependencies directly in the build directory
- [2025-12-10T11:26:05Z] `eb67454` Refactor Telegram webhook handler to include structured logging and response formatting

### Commits (develop) - oldest -> newest
- [2025-12-10T03:33:54Z] `4eac2f4` Initialize project structure with .gitignore, .python-version, README.md, main.py, and pyproject.toml
- [2025-12-10T11:02:07Z] `2d24fcc` PoC - merge 1 (#1)
- [2025-12-10T11:07:30Z] `4f7f9ef` Update Lambda deployment workflow to install dependencies directly in the build directory
- [2025-12-10T11:26:05Z] `eb67454` Refactor Telegram webhook handler to include structured logging and response formatting
- [2025-12-20T02:48:19Z] `61c6238` feat: initial database schema, telegram integration, and onboarding flow (#2)

### PRs - oldest -> newest (by created date)
- [2025-12-10T11:01:51Z] #1 [MERGED] PoC - merge 1 (`main` <- `PoC`) | merged: 2025-12-10T11:02:07Z | https://github.com/invaritech-ai/resto-pilot-backend/pull/1
- [2025-12-20T02:43:07Z] #2 [MERGED] feat: initial database schema, telegram integration, and onboarding flow (`develop` <- `infra/db-bootstrap`) | merged: 2025-12-20T02:48:19Z | https://github.com/invaritech-ai/resto-pilot-backend/pull/2

## resto-pilot-frontend
- default branch: main
- dev branch: (none)

### Commits (main) - oldest -> newest
- [2025-12-15T05:40:22Z] `e35a8ad` Initial commit from Create Next App
- [2025-12-15T14:54:58Z] `75403da` Refactor code structure for improved readability and maintainability
- [2025-12-15T15:29:57Z] `bb5ecbe` Refactor imports and improve code formatting for better readability

### Commits ((none)) - oldest -> newest
- (branch not present)

### PRs - oldest -> newest (by created date)
- [2026-01-22T16:12:35Z] #1 [OPEN] Add restaurant list view (`main` <- `feat/restaurant-list-view`) | https://github.com/invaritech-ai/resto-pilot-frontend/pull/1

