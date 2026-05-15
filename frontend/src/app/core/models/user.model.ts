export interface User {
  id: number;
  email: string;
  date_joined: string;
}

export interface AuthTokens {
  access: string;
  refresh: string;
}
