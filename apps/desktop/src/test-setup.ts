import '@testing-library/jest-dom/vitest'
import { configure } from '@testing-library/react'
import { beforeEach, vi } from 'vitest'

// Bounded DOM readiness for the supported low-memory Windows laptop. This
// changes neither product deadlines nor deterministic race-test ordering.
configure({ asyncUtilTimeout: 5000 })

// jsdom has no WebGL. Exercise the honest fallback; browser checks render GLSL.
beforeEach(() => { vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null) })
