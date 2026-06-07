import { type Page, type Locator } from "@playwright/test";

/**
 * Page Object for WS-16 · Profile.
 *
 * Covers the profile summary page (/profile), the welcome "needs-prompt"
 * modal (mounted globally in the app-shell, fires when nudge.form==='modal'
 * && nudge.due), the questionnaire form (/profile/questionnaire), the
 * inline fine-tune control and the questionnaire-history list.
 *
 * NB: the questionnaire is ONE scrolling form (sections rendered as
 * <fieldset>), not a step-wise wizard — there is no next/back affordance.
 * "Progress" + "preserve answers" are exercised by answering items in place.
 */
export class ProfilePage {
  constructor(public readonly page: Page) {}

  // ---- navigation ----------------------------------------------------------
  async goto(): Promise<void> {
    await this.page.goto("/profile");
  }
  async gotoQuestionnaire(): Promise<void> {
    await this.page.goto("/profile/questionnaire");
  }

  // ---- profile page: summary -----------------------------------------------
  heading(): Locator {
    return this.page.getByRole("heading", { level: 1, name: "Your investor profile" });
  }
  accountCardHeading(): Locator {
    return this.page.getByRole("heading", { level: 2, name: "Account" });
  }
  profileCardHeading(): Locator {
    return this.page.getByRole("heading", { level: 2, name: "Investor profile" });
  }
  /** Derived band pills, e.g. "Risk · Moderate", "Horizon · Long", "Patience · Low". */
  bandPill(text: string | RegExp): Locator {
    return this.page.getByText(text);
  }
  applyToRunsToggle(): Locator {
    return this.page.getByRole("checkbox", { name: "Apply my profile to new analyses" });
  }

  // ---- watchlist card (projected on /profile) ------------------------------
  watchlistCardHeading(): Locator {
    return this.page.getByRole("heading", { level: 2, name: "Watchlist" });
  }

  // ---- empty / no-profile state --------------------------------------------
  emptyState(): Locator {
    return this.page.getByText("You haven't taken the questionnaire yet.");
  }
  takeQuestionnaireCta(): Locator {
    return this.page.getByRole("link", { name: "Take the questionnaire" });
  }

  // ---- welcome (needs-prompt) modal ----------------------------------------
  welcomeModal(): Locator {
    return this.page.getByRole("dialog", { name: "Personalize your analyses" });
  }
  welcomeModalDismiss(): Locator {
    return this.welcomeModal().getByRole("button", { name: "Maybe later" });
  }
  welcomeModalStart(): Locator {
    return this.welcomeModal().getByRole("link", { name: "Take the questionnaire" });
  }

  // ---- questionnaire form --------------------------------------------------
  questionnaireHeading(): Locator {
    return this.page.getByRole("heading", { level: 1, name: "Investor Questionnaire" });
  }
  /** A single-choice radio group, addressed by its question label (aria-label). */
  radioGroup(label: string | RegExp): Locator {
    return this.page.getByRole("radiogroup", { name: label });
  }
  /** One radio inside the named group (option text === radio accessible name). */
  radioOption(group: string | RegExp, option: string | RegExp): Locator {
    return this.radioGroup(group).getByRole("radio", { name: option });
  }
  /** Multi-select chip checkbox (e.g. preferred sectors / avoid). */
  multiOption(option: string | RegExp): Locator {
    return this.page.getByRole("checkbox", { name: option });
  }
  modelSelect(): Locator {
    return this.page.getByLabel("Analysis model");
  }
  submitButton(): Locator {
    return this.page.getByRole("button", { name: /Analyze|Submitting|Analyzing/ });
  }
  cancelButton(): Locator {
    return this.page.getByRole("button", { name: "Cancel" });
  }
  formError(): Locator {
    return this.page.getByRole("alert");
  }

  // ---- fine-tune (on the profile card) -------------------------------------
  fineTuneButton(): Locator {
    return this.page.getByRole("button", { name: "Fine-tune" });
  }
  tuneRiskSelect(): Locator {
    return this.page.getByLabel("Risk band");
  }
  tuneHorizonSelect(): Locator {
    return this.page.getByLabel("Time horizon");
  }
  tunePatienceSelect(): Locator {
    return this.page.getByLabel("Patience");
  }
  tuneSaveButton(): Locator {
    return this.page.getByRole("button", { name: "Save changes" });
  }

  // ---- questionnaire history -----------------------------------------------
  viewHistoryButton(): Locator {
    return this.page.getByRole("button", { name: "View history" });
  }
  hideHistoryButton(): Locator {
    return this.page.getByRole("button", { name: "Hide history" });
  }
  /** Each history row is a <button> whose text includes the date + source + type. */
  historyRow(name: string | RegExp): Locator {
    return this.page.getByRole("button", { name });
  }

  // ---- recommended strategies ----------------------------------------------
  recommendedHeading(): Locator {
    return this.page.getByRole("heading", { level: 2, name: "Recommended strategies" });
  }
  /** "Create this strategy" CTA → /strategies/new?kind=<kind>. */
  createStrategyLink(): Locator {
    return this.page.getByRole("link", { name: "Create this strategy" });
  }
  /** "Learn more →" link → /info/<guide-slug>. */
  learnMoreLink(): Locator {
    return this.page.getByRole("link", { name: "Learn more" });
  }
}
