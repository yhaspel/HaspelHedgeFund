import { ConfirmService } from './confirm.service';

describe('ConfirmService', () => {
  let svc: ConfirmService;
  beforeEach(() => {
    svc = new ConfirmService();
  });

  it('starts with no active request', () => {
    expect(svc.active()).toBeNull();
  });

  it('exposes the pending request via active() while open', () => {
    svc.ask({ title: 'Halt all 3 accounts?', danger: true, requireText: 'HALT' });
    const a = svc.active();
    expect(a?.title).toBe('Halt all 3 accounts?');
    expect(a?.danger).toBe(true);
    expect(a?.requireText).toBe('HALT');
  });

  it('resolves true and clears active() on settle(true)', async () => {
    const p = svc.ask({ title: 'Confirm?' });
    svc.settle(true);
    expect(await p).toBe(true);
    expect(svc.active()).toBeNull();
  });

  it('resolves false on settle(false)', async () => {
    const p = svc.ask({ title: 'Confirm?' });
    svc.settle(false);
    expect(await p).toBe(false);
    expect(svc.active()).toBeNull();
  });

  it('cancels a still-open request (resolves false) when a new ask arrives', async () => {
    const first = svc.ask({ title: 'First?' });
    const second = svc.ask({ title: 'Second?' });
    expect(await first).toBe(false);
    expect(svc.active()?.title).toBe('Second?');
    svc.settle(true);
    expect(await second).toBe(true);
  });

  it('settle() with no active request is a no-op', () => {
    expect(() => svc.settle(true)).not.toThrow();
    expect(svc.active()).toBeNull();
  });
});
