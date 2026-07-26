import { api } from "./client";
import type { Container, ContainerAreas } from "../types";

export const listContainers = (token: string) =>
	api<{ containers: Container[] }>("/api/containers", token);

export const getContainer = (token: string, slug: string) =>
	api<Container>(`/api/containers/${slug}`, token);

export const listContainerAreas = (token: string, slug: string) =>
	api<ContainerAreas>(`/api/containers/${slug}/areas`, token);
