import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { BehaviorSubject, of, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { GraphsStore } from '../../abstraction/graphs.store';
import { ModelsStore } from '../../abstraction/models.store';
import { ConfirmService } from '../shared/confirm.service';
import { GraphEditorPage } from './graph-editor.page';
import { GraphsListPage } from './graphs-list.page';

/**
 * WP F1 —
 *  1. GraphEditorPage read `route.snapshot` once, so an in-place
 *     /graphs/1/edit → /graphs/2/edit left graph 1's nodes on the canvas while
 *     `graphId` said 2 — and the localStorage autosave (keyed by graph id) then
 *     wrote graph 1's nodes into graph 2's draft.
 *  2. GraphsListPage's "Archive" button is a `DELETE /graphs/<id>/`: it needs a
 *     confirmation that says so, and a failure the user can see.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Cmp = any;

function editor(params: BehaviorSubject<ReturnType<typeof convertToParamMap>>, store: Partial<GraphsStore>): Cmp {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      {
        provide: GraphsStore,
        useValue: {
          registry: signal(null).asReadonly(),
          loadRegistry: () => of(null),
          getGraph: (id: number) => of({ id, name: `Graph ${id}` }),
          listVersions: () => of([]),
          getVersion: () => of(null),
          ...store,
        },
      },
      {
        provide: ModelsStore,
        useValue: { models: signal([]).asReadonly(), loadModels: () => of({ models: [] }) },
      },
      {
        provide: ActivatedRoute,
        useValue: { paramMap: params.asObservable(), snapshot: { paramMap: params.value } },
      },
    ],
  });
  return TestBed.runInInjectionContext(() => new GraphEditorPage());
}

describe('fix-f1 · GraphEditorPage follows the :id', () => {
  it('reloads the graph when the route param changes in place', () => {
    const params = new BehaviorSubject(convertToParamMap({ id: '1' }));
    const getGraph = vi.fn((id: number) => of({ id, name: `Graph ${id}` }));
    const page = editor(params, { getGraph } as unknown as Partial<GraphsStore>);
    page.ngOnInit();
    expect(page.graphId()).toBe(1);
    expect(getGraph).toHaveBeenCalledWith(1);

    params.next(convertToParamMap({ id: '2' }));
    expect(page.graphId()).toBe(2);
    expect(getGraph).toHaveBeenCalledWith(2);
    // The previous graph's working state is dropped, so the id-keyed autosave
    // cannot write graph 1's nodes into graph 2's draft.
    expect(page.workingNodes()).toEqual([]);
    expect(page.graphName()).toBe('Graph 2');
    page.ngOnDestroy();
  });

  it('does not re-fetch when paramMap re-emits the same id', () => {
    const params = new BehaviorSubject(convertToParamMap({ id: '1' }));
    const getGraph = vi.fn((id: number) => of({ id, name: `Graph ${id}` }));
    const page = editor(params, { getGraph } as unknown as Partial<GraphsStore>);
    page.ngOnInit();
    params.next(convertToParamMap({ id: '1' }));
    expect(getGraph).toHaveBeenCalledTimes(1);
    page.ngOnDestroy();
  });

  it('surfaces a load failure with a retry instead of an empty canvas', () => {
    const params = new BehaviorSubject(convertToParamMap({ id: '1' }));
    const page = editor(params, {
      getGraph: () => throwError(() => ({ status: 500, error: { detail: 'graph service down' } })),
    } as unknown as Partial<GraphsStore>);
    page.ngOnInit();
    expect(page.loadError()).toBe('graph service down');
    page.ngOnDestroy();
  });

  it('stops following the route after ngOnDestroy', () => {
    const params = new BehaviorSubject(convertToParamMap({ id: '1' }));
    const getGraph = vi.fn((id: number) => of({ id, name: `Graph ${id}` }));
    const page = editor(params, { getGraph } as unknown as Partial<GraphsStore>);
    page.ngOnInit();
    page.ngOnDestroy();
    params.next(convertToParamMap({ id: '3' }));
    expect(getGraph).toHaveBeenCalledTimes(1);
  });
});

describe('fix-f1 · GraphsListPage "Archive" is a delete', () => {
  function list(archiveResult: unknown, ask = vi.fn(() => Promise.resolve(true))): { page: Cmp; ask: typeof ask } {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        {
          provide: GraphsStore,
          useValue: {
            graphs: signal([]).asReadonly(),
            loadGraphs: () => of([]),
            archiveGraph: () => archiveResult,
          },
        },
        { provide: ConfirmService, useValue: { ask } },
      ],
    });
    return { page: TestBed.runInInjectionContext(() => new GraphsListPage()), ask };
  }

  const graph = { id: 5, name: 'Council v2' } as never;

  it('asks for confirmation and says the graph and its versions are removed', async () => {
    const { page, ask } = list(of(undefined));
    await page.archive(graph);
    expect(ask).toHaveBeenCalledTimes(1);
    const opts = (ask.mock.calls[0] as unknown as [{ title: string; body: string; danger: boolean }])[0];
    expect(opts.title).toContain('Delete the graph "Council v2"');
    expect(opts.body).toContain('saved versions');
    expect(opts.danger).toBe(true);
  });

  it('does nothing when the confirmation is declined', async () => {
    const archiveGraph = vi.fn(() => of(undefined));
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        {
          provide: GraphsStore,
          useValue: { graphs: signal([]).asReadonly(), loadGraphs: () => of([]), archiveGraph },
        },
        { provide: ConfirmService, useValue: { ask: () => Promise.resolve(false) } },
      ],
    });
    const page: Cmp = TestBed.runInInjectionContext(() => new GraphsListPage());
    await page.archive(graph);
    expect(archiveGraph).not.toHaveBeenCalled();
  });

  it('surfaces a refused delete', async () => {
    const { page } = list(throwError(() => ({ status: 409, error: { detail: 'graph is in use' } })));
    await page.archive(graph);
    expect(page.error()).toBe('graph is in use');
  });
});
