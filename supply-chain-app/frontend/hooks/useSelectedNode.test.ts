import { act, renderHook } from '@testing-library/react'
import useSelectedNode from './useSelectedNode'

const mockNode = {
  id: '1',
  label: 'TSMC',
  tier: 1,
  country: 'Taiwan',
  risk_level: 'high' as const,
  replaceability: 'low' as const,
  description: '3nm foundry',
  role: 'Foundry',
}

test('initially no node is selected', () => {
  const { result } = renderHook(() => useSelectedNode())
  expect(result.current.selectedNode).toBeNull()
})

test('selectNode updates selectedNode', () => {
  const { result } = renderHook(() => useSelectedNode())
  act(() => result.current.selectNode(mockNode))
  expect(result.current.selectedNode?.label).toBe('TSMC')
})

test('clearNode resets selectedNode to null', () => {
  const { result } = renderHook(() => useSelectedNode())
  act(() => result.current.selectNode(mockNode))
  act(() => result.current.clearNode())
  expect(result.current.selectedNode).toBeNull()
})

test('selectNode replacing previous selection', () => {
  const { result } = renderHook(() => useSelectedNode())
  act(() => result.current.selectNode(mockNode))
  act(() => result.current.selectNode({ ...mockNode, id: '2', label: 'SK Hynix' }))
  expect(result.current.selectedNode?.label).toBe('SK Hynix')
})
