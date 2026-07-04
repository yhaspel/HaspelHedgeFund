import { Component, signal } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { ConfirmHostComponent } from './presentation/shared/confirm-host.component';
import { OfflineBannerComponent } from './presentation/shared/offline-banner.component';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, ConfirmHostComponent, OfflineBannerComponent],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class App {
  protected readonly title = signal('frontend');
}
