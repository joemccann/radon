/** @vitest-environment jsdom */
import {createElement} from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen} from '@testing-library/react';
import {MarkerAccent} from '../components/atoms/MarkerAccent';
vi.mock('metal-fx',()=>({MetalFx:()=>{throw new Error('metal-fx: WebGL not supported');}}));
vi.mock('../components/atoms/useSiteFxTheme',()=>({useSiteFxTheme:()=> 'light'}));
afterEach(()=>{cleanup();vi.restoreAllMocks();});
it('preserves the milestone and surrounding page when the decorative GPU renderer fails',()=>{
 vi.spyOn(console,'error').mockImplementation(()=>{});
 render(createElement('main',null,createElement('h1',null,'Research and execution'),createElement(MarkerAccent,null,'Review evidence')));
 expect(screen.getByRole('heading').textContent).toBe('Research and execution');
 expect(screen.getByTestId('marker-accent').textContent).toBe('Review evidence');
});
