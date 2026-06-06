import { Component, signal } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { ConfirmHostComponent } from './presentation/shared/confirm-host.component';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, ConfirmHostComponent],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class App {
  protected readonly title = signal('frontend');
}
