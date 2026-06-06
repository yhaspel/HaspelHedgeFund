import { ConfirmService } from './confirm.service';

describe('ConfirmService', () => {
  let svc: ConfirmService;
  beforeEach(() => {
    svc = new ConfirmService();
  });

  it('starts with no active request', () => {
    expect(svc.active()).toBeNull();
  });

  it('ask(): exposes the request, resolves true on accept, clears active()', async () => {
    const p = svc.ask({ title: 'Halt all 3 accounts?', danger: true, requireText: 'HALT' });
    const a = svc.active();
    expect(a?.kind).toBe('confirm');
    expect(a?.title).toBe('Halt all 3 accounts?');
    expect(a?.danger).toBe(true);
    expect(a?.requireText).toBe('HALT');
    svc.accept(true);
    expect(await p).toBe(true);
    expect(svc.active()).toBeNull();
  });

  it('ask(): resolves false on cancel', async () => {
    const p = svc.ask({ title: 'Confirm?' });
    svc.cancel();
    expect(await p).toBe(false);
  });

  it('notify(): single-action info, resolves on accept', async () => {
    const p = svc.notify({ title: 'Could not delete.' });
    expect(svc.active()?.kind).toBe('notify');
    svc.accept(true);
    await p; // resolves void
    expect(svc.active()).toBeNull();
  });

  it('askText(): resolves the typed value on accept', async () => {
    const p = svc.askText({ title: 'Rename list', initialValue: 'Old' });
    expect(svc.active()?.kind).toBe('prompt');
    expect(svc.active()?.initialValue).toBe('Old');
    svc.accept('New name');
    expect(await p).toBe('New name');
  });

  it('askText(): resolves null on cancel', async () => {
    const p = svc.askText({ title: 'Rename list' });
    svc.cancel();
    expect(await p).toBeNull();
  });

  it('a new request cancels the one already open (with its cancel value)', async () => {
    const first = svc.ask({ title: 'First?' });
    const second = svc.askText({ title: 'Second?' });
    expect(await first).toBe(false);
    expect(svc.active()?.title).toBe('Second?');
    svc.accept('x');
    expect(await second).toBe('x');
  });

  it('accept()/cancel() with no active request are no-ops', () => {
    expect(() => svc.accept(true)).not.toThrow();
    expect(() => svc.cancel()).not.toThrow();
    expect(svc.active()).toBeNull();
  });
});
