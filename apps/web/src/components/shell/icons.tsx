// Cohesive Apple/SF-Symbols-style line icons used across the whole app.
// Stroke = currentColor so each icon inherits its surrounding text color;
// uniform 1.7 stroke, rounded caps/joins, 24px grid.
import type React from "react";

type IconProps = { size?: number };

function Svg({
	size = 18,
	children,
}: {
	size?: number;
	children: React.ReactNode;
}) {
	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={1.7}
			strokeLinecap="round"
			strokeLinejoin="round"
			aria-hidden="true"
		>
			{children}
		</svg>
	);
}

export const IconDesign = ({ size }: IconProps) => (
	<Svg size={size}>
		<rect x="3" y="3" width="7" height="7" rx="1.5" />
		<rect x="14" y="3" width="7" height="7" rx="3.5" />
		<rect x="3" y="14" width="7" height="7" rx="3.5" />
		<rect x="14" y="14" width="7" height="7" rx="1.5" />
	</Svg>
);

export const IconVideo = ({ size }: IconProps) => (
	<Svg size={size}>
		<rect x="3.5" y="5" width="12.5" height="14" rx="2" />
		<path d="M16 10l4.5-3v10L16 14" />
		<path d="M7.5 9.5v5l4-2.5-4-2.5z" />
	</Svg>
);

export const IconNewChat = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M20 11.5V17a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3h5.5" />
		<path d="M18.4 3.6a1.9 1.9 0 0 1 2.7 2.7L13 14.4l-3.5.8.8-3.5 8.1-8.1z" />
	</Svg>
);

export const IconProjects = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M3 7a2 2 0 0 1 2-2h3.4a2 2 0 0 1 1.4.6L11 7h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
	</Svg>
);
export const IconFolder = IconProjects;

export const IconAgents = ({ size }: IconProps) => (
	<Svg size={size}>
		<circle cx="12" cy="12" r="9" />
		<circle cx="12" cy="10" r="3" />
		<path d="M6.5 18.4a6 6 0 0 1 11 0" />
	</Svg>
);

export const IconUsers = ({ size }: IconProps) => (
	<Svg size={size}>
		<circle cx="9" cy="8" r="3.2" />
		<path d="M2.8 19a6.2 6.2 0 0 1 12.4 0" />
		<path d="M16 5.4a3 3 0 0 1 0 5.6" />
		<path d="M17.4 13.2a5.5 5.5 0 0 1 3.8 5.6" />
	</Svg>
);

export const IconGear = ({ size }: IconProps) => (
	<Svg size={size}>
		<circle cx="12" cy="12" r="3.2" />
		<path d="M19.4 13a7.6 7.6 0 0 0 0-2l2-1.5-2-3.5-2.4 1a7.6 7.6 0 0 0-1.7-1L15 3h-4l-.3 2.5a7.6 7.6 0 0 0-1.7 1l-2.4-1-2 3.5 2 1.5a7.6 7.6 0 0 0 0 2l-2 1.5 2 3.5 2.4-1a7.6 7.6 0 0 0 1.7 1L11 21h4l.3-2.5a7.6 7.6 0 0 0 1.7-1l2.4 1 2-3.5z" />
	</Svg>
);

export const IconPanelRight = ({ size }: IconProps) => (
	<Svg size={size}>
		<rect x="3" y="5" width="18" height="14" rx="2" />
		<path d="M15 5v14" />
	</Svg>
);

export const IconPanelLeft = ({ size }: IconProps) => (
	<Svg size={size}>
		<rect x="3" y="5" width="18" height="14" rx="2" />
		<path d="M9 5v14" />
	</Svg>
);

export const IconMenu = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M4 7h16M4 12h16M4 17h16" />
	</Svg>
);

export const IconSearch = ({ size }: IconProps) => (
	<Svg size={size}>
		<circle cx="11" cy="11" r="7" />
		<path d="M21 21l-4.3-4.3" />
	</Svg>
);

export const IconPlus = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M12 5v14M5 12h14" />
	</Svg>
);

export const IconSend = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M4 12h15" />
		<path d="M13 6l6 6-6 6" />
	</Svg>
);

export const IconClose = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M6 6l12 12M18 6L6 18" />
	</Svg>
);

export const IconPencil = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M4 20l4-1L19 8a1.9 1.9 0 0 0-2.7-2.7L5 16.5 4 20z" />
		<path d="M14.5 6.5l3 3" />
	</Svg>
);

export const IconTrash = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M4.5 7h15" />
		<path d="M9 7V5.2a1.2 1.2 0 0 1 1.2-1.2h3.6A1.2 1.2 0 0 1 15 5.2V7" />
		<path d="M6.5 7l.8 11a2 2 0 0 0 2 1.9h5.4a2 2 0 0 0 2-1.9l.8-11" />
		<path d="M10 11v5M14 11v5" />
	</Svg>
);

export const IconFile = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
		<path d="M14 3v5h5" />
	</Svg>
);

export const IconHome = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M3 10.5 12 3l9 7.5" />
		<path d="M5 9.5V20a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9.5" />
	</Svg>
);

export const IconMonitor = ({ size }: IconProps) => (
	<Svg size={size}>
		<rect x="2.5" y="4" width="19" height="12" rx="2" />
		<path d="M9 20h6M12 16v4" />
	</Svg>
);

export const IconTablet = ({ size }: IconProps) => (
	<Svg size={size}>
		<rect x="5" y="2.5" width="14" height="19" rx="2.5" />
		<path d="M11.5 18.5h1" />
	</Svg>
);

export const IconMobile = ({ size }: IconProps) => (
	<Svg size={size}>
		<rect x="7" y="2.5" width="10" height="19" rx="2.5" />
		<path d="M11.5 18.5h1" />
	</Svg>
);

export const IconTerminal = ({ size }: IconProps) => (
	<Svg size={size}>
		<rect x="3" y="4" width="18" height="16" rx="2" />
		<path d="M7 9l3 3-3 3" />
		<path d="M13 15h4" />
	</Svg>
);

export const IconFilePlus = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h5" />
		<path d="M13 3v5h5" />
		<path d="M17 13.5v6M14 16.5h6" />
	</Svg>
);

export const IconFolderPlus = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M3 7a2 2 0 0 1 2-2h3.4a2 2 0 0 1 1.4.6L11 7h6a2 2 0 0 1 2 2v3" />
		<path d="M3 7v10a2 2 0 0 0 2 2h6" />
		<path d="M17 14.5v6M14 17.5h6" />
	</Svg>
);

export const IconChevronRight = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M9.5 6l6 6-6 6" />
	</Svg>
);

export const IconChevronLeft = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M14.5 6l-6 6 6 6" />
	</Svg>
);

export const IconCopy = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M11 9h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-7a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z" />
		<path d="M5 15a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2" />
	</Svg>
);

export const IconCheck = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M5 13l4 4L19 7" />
	</Svg>
);

export const IconArrowDown = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M12 5v14M6 13l6 6 6-6" />
	</Svg>
);

export const IconSparkle = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M12 3l1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7z" />
	</Svg>
);

export const IconAudit = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M12 3l7 3v5c0 4.4-3 7.6-7 9-4-1.4-7-4.6-7-9V6l7-3z" />
		<path d="M9 12l2 2 4-4" />
	</Svg>
);

export const IconTasks = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M9 6h11M9 12h11M9 18h11" />
		<path d="M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2" />
	</Svg>
);

export const IconArtifacts = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M21 8l-9-5-9 5 9 5 9-5z" />
		<path d="M3 8v8l9 5 9-5V8" />
		<path d="M12 13v8" />
	</Svg>
);

export const IconWiki = ({ size }: IconProps) => (
	<Svg size={size}>
		<rect x="5" y="3.5" width="14" height="17" rx="2" />
		<path d="M9 3.5v17" />
		<path d="M12 8.5h4M12 12h4" />
	</Svg>
);

export const IconWorkflows = ({ size }: IconProps) => (
	<Svg size={size}>
		<rect x="3" y="4" width="6" height="5" rx="1.5" />
		<rect x="15" y="9.5" width="6" height="5" rx="1.5" />
		<rect x="3" y="15" width="6" height="5" rx="1.5" />
		<path d="M9 6.5h3a2 2 0 0 1 2 2v3.5" />
		<path d="M9 17.5h3a2 2 0 0 0 2-2V12" />
	</Svg>
);

export const IconActivity = ({ size }: IconProps) => (
	<Svg size={size}>
		<path d="M3 12h3.5l2-6 4 13 2.5-7H21" />
	</Svg>
);
