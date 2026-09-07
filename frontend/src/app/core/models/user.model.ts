export interface User {
  id: number;
  email: string;
  date_joined: string;
  /**
   * Staff can run the catalog-wide OpenRouter actions
   * (`POST /api/models/fetch/`, `POST /api/models/verify-pricing/` — 403 for
   * everyone else). Optional because `/me/` does not expose it yet: when it is
   * `undefined` the UI keeps the buttons enabled and reports the 403 clearly.
   * See "Cross-WP requests" — apps/accounts/serializers.py UserSerializer.
   */
  is_staff?: boolean;
}

export interface AuthTokens {
  access: string;
  refresh: string;
}
