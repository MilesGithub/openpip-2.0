// A module of its own, since jsdom's location.reload cannot be spied on: a test mocks this seam.
export function reloadDocument(): void { window.location.reload() }
