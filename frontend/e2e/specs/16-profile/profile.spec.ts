import { test, expect } from "../../fixtures";

/**
 * WS-16 · Investor profile + questionnaire.
 *
 * The questionnaire is a single scrolling form (no step-wise wizard), so the
 * plan's "progress through items / back-next preserve answers" rows are
 * exercised as: answer items in place, verify required-gating on submit, and
 * verify a chosen closed-enum option stays checked while other groups change.
 */
test.describe("WS-16 · Profile", () => {
  test("PR-01 profile page renders summary + watchlist + derived bands/traits", async ({ profile }) => {
    await profile.goto();

    await expect(profile.heading()).toBeVisible();
    await expect(profile.accountCardHeading()).toBeVisible();
    await expect(profile.profileCardHeading()).toBeVisible();

    // Derived investor type + summary from the profile fixture.
    await expect(profile.page.getByText("Experienced Investor", { exact: true })).toBeVisible();
    await expect(
      profile.page.getByText("Experienced investor seeking balanced growth"),
    ).toBeVisible();

    // Derived bands rendered as pills (labels come from *_BAND_LABEL maps).
    await expect(profile.bandPill(/Risk · Moderate/)).toBeVisible();
    await expect(profile.bandPill(/Horizon · Long/)).toBeVisible();
    await expect(profile.bandPill(/Patience · Low/)).toBeVisible();

    // Insight + constraint traits surfaced from analysis.
    await expect(
      profile.page.getByText("Has a medium-to-long term investment horizon"),
    ).toBeVisible();
    await expect(profile.page.getByText(/Constraints:.*Avoid Tobacco/)).toBeVisible();

    // Apply-to-runs control + watchlist card both present.
    await expect(profile.applyToRunsToggle()).toBeChecked();
    await expect(profile.watchlistCardHeading()).toBeVisible();
  });

  test("PR-02 welcome modal shows for an un-profiled user → dismiss + start questionnaire", async ({
    profile,
    apiMock,
  }) => {
    // No-profile bundle that also marks the welcome nudge due as a modal.
    apiMock.override("GET", "/profile/", {
      json: {
        user: { id: 1, email: "e2e@example.com", joined_at: null },
        has_questionnaire: false,
        active: null,
        latest: null,
        state: {
          apply_to_runs: true,
          nudge_dismiss_count: 0,
          nudge_last_dismissed_at: null,
          updated_at: "2026-06-06T00:00:00Z",
        },
        nudge: { due: true, form: "modal" },
        schema_version: 1,
      },
    });
    // Dismissing posts to an endpoint outside the default registry.
    apiMock.override("POST", "/profile/nudge/dismiss/", { json: { ok: true } });

    await profile.goto();

    // Welcome modal appears (mounted globally in the app-shell).
    await expect(profile.welcomeModal()).toBeVisible();
    // It offers a way into the questionnaire…
    await expect(profile.welcomeModalStart()).toHaveAttribute(
      "href",
      /\/profile\/questionnaire/,
    );
    // …and the empty-state CTA is behind it on the page.
    await expect(profile.emptyState()).toBeVisible();

    // Dismiss closes the modal.
    await profile.welcomeModalDismiss().click();
    await expect(profile.welcomeModal()).toBeHidden();
  });

  test("PR-03 questionnaire — required gating then submit", async ({ page, profile, apiMock }) => {
    // Default POST returns status:'derived' (no analysis_status) → the page
    // would poll; force a terminal 'done' so submit navigates back to /profile.
    apiMock.override("POST", "/profile/questionnaire/", {
      status: 201,
      json: { id: 9700, analysis_status: "done" },
    });

    await profile.gotoQuestionnaire();
    await expect(profile.questionnaireHeading()).toBeVisible();

    // Submitting with required questions blank surfaces a gating error.
    await profile.submitButton().click();
    await expect(profile.formError()).toBeVisible();
    await expect(profile.formError()).toContainText(/Please answer/);
    await expect(page).toHaveURL(/\/profile\/questionnaire/);

    // Answer every required single-choice question (first option of each group).
    for (const label of [
      "Your age range",
      "How would you describe your investing experience?",
      "Your primary goal for this money?",
      "When do you expect to need most of this money?",
      "How would you rate your appetite for risk?",
      "Your portfolio falls 25% in a month. What do you do?",
      "Largest one-year loss you could tolerate without losing sleep",
      "A position is underwater but the thesis holds. How long do you hold?",
      "How often do you typically trade?",
      "What drives your decisions most?",
      "Which is closer to your style?",
    ]) {
      await profile.radioGroup(label).getByRole("radio").first().check();
    }

    await profile.submitButton().click();
    // POST /profile/questionnaire/ → done → navigate to the summary page.
    await expect(page).toHaveURL(/\/profile$/);
  });

  test("PR-04 closed-enum choices selectable + answers preserved across groups", async ({ profile }) => {
    await profile.gotoQuestionnaire();
    await expect(profile.questionnaireHeading()).toBeVisible();

    const age = profile.radioOption("Your age range", "Under 25");
    const risk = profile.radioOption("How would you rate your appetite for risk?", "Moderate");

    // Pick one option in the first group.
    await age.check();
    await expect(age).toBeChecked();

    // Selecting within a *different* group must not clear the first.
    await risk.check();
    await expect(risk).toBeChecked();
    await expect(age).toBeChecked();

    // Re-selecting another option in the first group swaps cleanly.
    const olderAge = profile.radioOption("Your age range", "45–54");
    await olderAge.check();
    await expect(olderAge).toBeChecked();
    await expect(age).not.toBeChecked();
    // The other group's answer is still preserved.
    await expect(risk).toBeChecked();
  });

  test("PR-05 fine-tune bands adjusts within range + persists", async ({ profile, apiMock }) => {
    let tuned = false;
    apiMock.override("POST", "/profile/questionnaire/:id/tune/", {
      json: { ok: true },
    });
    profile.page.on("request", (r) => {
      if (r.method() === "POST" && /\/profile\/questionnaire\/\d+\/tune\/$/.test(r.url())) {
        tuned = true;
      }
    });

    await profile.goto();
    await profile.fineTuneButton().click();

    // Three bounded <select> controls appear.
    await expect(profile.tuneRiskSelect()).toBeVisible();
    await expect(profile.tuneHorizonSelect()).toBeVisible();
    await expect(profile.tunePatienceSelect()).toBeVisible();

    // Change a band to a different in-range value, then save.
    await profile.tuneRiskSelect().selectOption({ label: "Aggressive" });
    await expect(profile.tuneRiskSelect()).toHaveValue("aggressive");
    await profile.tuneSaveButton().click();

    // Save POSTs the tune endpoint and the panel closes (store reloads).
    await expect.poll(() => tuned).toBe(true);
    await expect(profile.tuneSaveButton()).toBeHidden();
  });

  test("PR-06 questionnaire history lists prior submissions", async ({ profile, apiMock }) => {
    // History endpoint is not in the default registry — supply it per-test.
    apiMock.override("GET", "/profile/questionnaire/history/", {
      json: {
        items: [
          {
            id: 3,
            source: "questionnaire",
            created_at: "2026-05-23T12:48:43Z",
            model_id: "openrouter:meta-llama/llama-3.3-70b-instruct",
            analysis_status: "done",
            investor_type: "Experienced Investor",
          },
          {
            id: 4,
            source: "tuned",
            created_at: "2026-05-24T09:00:00Z",
            model_id: "openrouter:meta-llama/llama-3.3-70b-instruct",
            analysis_status: "done",
            investor_type: "Balanced Investor",
          },
        ],
      },
    });

    await profile.goto();
    await profile.viewHistoryButton().click();

    // Both prior submissions render as selectable rows.
    await expect(profile.historyRow(/Experienced Investor/)).toBeVisible();
    await expect(profile.historyRow(/Balanced Investor/)).toBeVisible();
    // Toggle collapses the panel.
    await profile.hideHistoryButton().click();
    await expect(profile.historyRow(/Balanced Investor/)).toBeHidden();
  });

  test("PR-07 strategy recommendations render + link to a strategy kind", async ({ profile }) => {
    await profile.goto();

    await expect(profile.recommendedHeading()).toBeVisible();
    // Recommended kinds from the fixture (label-mapped).
    await expect(profile.page.getByText("Long-only", { exact: true })).toBeVisible();
    await expect(
      profile.page.getByText("Balanced growth goal and moderate risk tolerance"),
    ).toBeVisible();

    // "Create this strategy" carries the kind through to the new-strategy form.
    await expect(profile.createStrategyLink().first()).toHaveAttribute(
      "href",
      /\/strategies\/new\?kind=long_only/,
    );
    // "Learn more" deep-links to the strategy-kind guide.
    await expect(profile.learnMoreLink().first()).toHaveAttribute(
      "href",
      /\/info\/long-only/,
    );
  });
});
