/** @vitest-environment jsdom */
import {createElement} from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen} from '@testing-library/react';
import {MarkerFallback} from '../components/atoms/MarkerFallback';
function UnavailableRenderer(): never { throw new Error('WebGL not supported'); }
afterEach(()=>{cleanup();vi.restoreAllMocks();});
it('preserves the milestone and surrounding page when the decorative GPU renderer fails',()=>{
 vi.spyOn(console,'error').mockImplementation(()=>{});
 render(createElement('main',null,createElement('h1',null,'Research and execution'),createElement(MarkerFallback,{fallback:createElement('span',{'data-testid':'marker-accent'},'Review evidence'),children:createElement(UnavailableRenderer)})));
 expect(screen.getByRole('heading').textContent).toBe('Research and execution');
 expect(screen.getByTestId('marker-accent').textContent).toBe('Review evidence');
});
