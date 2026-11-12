/** The dose panel's id on the zone page (DoseCard sets it), so Dose elsewhere can open the page
    there. Kept out of ZoneActions.tsx, which may only export components (fast refresh). */
export const DOSE_CARD = "dose-card";

/** Scroll to the dose panel on the zone page (jsdom has no scrollIntoView, hence the ?.). */
export const goToDoseCard = () => document.getElementById(DOSE_CARD)?.scrollIntoView?.({ block: "start" });
