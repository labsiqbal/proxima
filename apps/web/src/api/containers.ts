import { api } from "./client";
import type { Container, ContainerAreas } from "../types";

export const listContainers = (token: string, signal?: AbortSignal) =>
	api<{ containers: Container[] }>("/api/containers", token, { signal });

export const getContainer = (token: string, slug: string) =>
	api<Container>(`/api/containers/${slug}`, token);

export const listContainerAreas = (
	token: string,
	slug: string,
	signal?: AbortSignal,
) =>
	api<ContainerAreas>(`/api/containers/${slug}/areas`, token, { signal });
