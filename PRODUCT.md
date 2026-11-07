# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Whoever a mission assigns that transfer: assistants, office missionaries or office staff. People change often and usually aren't technical, so nobody can be expected to remember the setup. They use it weekly on a mission office Windows PC.

## Product Purpose

Referral Dash turns a mission's Tableau referral data into dashboard pictures and district summaries. Success is one click on the run button, followed by reports in a folder and optionally in the person's own Google Drive.

## Positioning

It is the shareable sibling of the Referral Care Data Hub: any mission can run it on its own PC with its own Tableau account and its own Drive, with no server.

## Operating Context

A local web page served on 127.0.0.1 and shown in an Edge or Chrome app window. A run signs in to Tableau, downloads the referral list, renders pictures, and uploads them if Drive is connected. The output is shared with the mission as pictures.

## Capabilities and Constraints

- Main screen holds the run action, where reports save (folder and Drive), and past reports. The owner asked for it to stay simple.
- First-time setup asks for the Church account and the mission name.
- Under Settings: what reports cover, what they compare to, how many sets to keep, thresholds, zone time zones, the Tableau link, and showing the browser. Date controls must stay under Settings, not on the main screen.
- A run can pause for an authenticator; the screen must make waiting on the person obvious.
- Drive connects by picking an existing folder, often in a shared drive; Google's chooser calls its button "Insert". No create-folder option: the owner removed it.
- Single-file Windows build with fonts served locally. No external requests from the app window.

## Brand Commitments

Name: Referral Dash. Existing app icon stays. The app keeps its original dark look, which matches the report pictures (Cal Sans and Nunito, night-blue cards, cyan accent): on 2026-11-07 the owner tried an Apple-style redesign and asked to undo it and "keep the old method". Within that look, words stay few, settings read quickly, and motion is smooth but small.

## Evidence on Hand

Real report pictures produced by the app, and the sample data in `referral_dash/sample-data`. No testimonials or usage figures exist.

## Product Principles

1. One action is obvious: the run.
2. A label or a number beats a sentence.
3. Someone who has never seen it can finish setup without help.
4. When a run stops or needs them, the screen says exactly what to do.

## Accessibility & Inclusion

No product-specific requirement established beyond keyboard use and readable contrast.
