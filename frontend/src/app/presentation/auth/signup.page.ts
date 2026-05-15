import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AuthStore } from '../../abstraction/auth.store';

@Component({
  selector: 'hf-signup',
  standalone: true,
  imports: [FormsModule, RouterLink],
  template: `
    <div class="min-h-screen flex items-center justify-center bg-gray-50">
      <form
        (ngSubmit)="submit()"
        class="bg-white p-8 rounded shadow w-96 space-y-4"
      >
        <h1 class="text-xl font-semibold">Sign up</h1>
        <input
          type="email"
          name="email"
          [(ngModel)]="email"
          placeholder="Email"
          class="w-full border rounded px-3 py-2"
          required
        />
        <input
          type="password"
          name="password"
          [(ngModel)]="password"
          placeholder="Password (min 8 chars)"
          class="w-full border rounded px-3 py-2"
          required
          minlength="8"
        />
        @if (error()) {
          <p class="text-red-600 text-sm">{{ error() }}</p>
        }
        <button class="w-full bg-blue-600 text-white rounded py-2">
          Sign up
        </button>
        <p class="text-sm text-gray-600">
          Have an account?
          <a routerLink="/login" class="text-blue-600">Log in</a>
        </p>
      </form>
    </div>
  `,
})
export class SignupPage {
  private readonly auth = inject(AuthStore);
  private readonly router = inject(Router);
  email = '';
  password = '';
  error = signal<string | null>(null);

  submit(): void {
    this.error.set(null);
    this.auth.signup(this.email, this.password).subscribe({
      next: () => {
        this.auth.login(this.email, this.password).subscribe({
          next: () => this.router.navigateByUrl('/'),
        });
      },
      error: () => this.error.set('Could not create account'),
    });
  }
}
