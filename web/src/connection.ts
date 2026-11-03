import { derived, writable } from 'svelte/store';
import type { ConnectionSummary } from './api';

export const connection = writable<ConnectionSummary | null>(null);
export const nanoAvailable = derived(connection, value => value?.configured ? value.nano_available : true);
