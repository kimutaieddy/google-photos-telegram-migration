// CloudWest Photos Client Application

let currentPhotos = [];
let currentIndex = -1;
let currentFilter = { album: null, year: null, search: null };
let currentOffset = 0;
let hasMore = true;
let isLoading = false;
const PAGE_SIZE = 60;

// Elements
const photosGrid = document.getElementById('photos-grid');
const albumsGrid = document.getElementById('albums-grid');
const photosSection = document.getElementById('photos-section');
const albumsSection = document.getElementById('albums-section');
const loadingIndicator = document.getElementById('loading-indicator');
const emptyState = document.getElementById('empty-state');
const statsBadge = document.getElementById('stats-badge');
const searchInput = document.getElementById('search-input');
const timelineYears = document.getElementById('timeline-years');
const lightbox = document.getElementById('lightbox');
const lightboxImg = document.getElementById('lightbox-img');
const lightboxVideo = document.getElementById('lightbox-video');
const lightboxClose = document.getElementById('lightbox-close');
const btnPrev = document.getElementById('btn-prev');
const btnNext = document.getElementById('btn-next');
const btnDownload = document.getElementById('btn-download');

// Metadata elements
const metaFilename = document.getElementById('meta-filename');
const metaDate = document.getElementById('meta-date');
const metaAlbum = document.getElementById('meta-album');
const metaSize = document.getElementById('meta-size');
const metaSha256 = document.getElementById('meta-sha256');
const metaMsgId = document.getElementById('meta-msgid');

// Helper: Format bytes
function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// Fetch stats & populate timeline
async function initSidebarAndFilters() {
  try {
    const statsRes = await fetch('/api/stats');
    const stats = await statsRes.json();
    if (stats.uploaded_items) {
      statsBadge.textContent = `${stats.uploaded_items.toLocaleString()} photos`;
    }

    const yearsRes = await fetch('/api/years');
    const years = await yearsRes.json();
    timelineYears.innerHTML = '';
    years.forEach(y => {
      const btn = document.createElement('button');
      btn.className = 'px-2 py-0.5 rounded text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800 transition-colors';
      btn.textContent = `${y.year}`;
      btn.onclick = () => {
        document.querySelectorAll('#subbar button').forEach(b => b.classList.remove('bg-neutral-800', 'text-neutral-100'));
        btn.classList.add('bg-neutral-800', 'text-neutral-100');
        currentFilter.year = y.year;
        currentFilter.album = null;
        resetAndLoadPhotos();
      };
      timelineYears.appendChild(btn);
    });
  } catch (err) {
    console.error('Failed to init filters:', err);
  }
}

// Fetch photos with pagination
async function loadPhotos() {
  if (isLoading || !hasMore) return;
  isLoading = true;
  loadingIndicator.classList.remove('hidden');

  const params = new URLSearchParams({
    limit: PAGE_SIZE,
    offset: currentOffset,
  });
  if (currentFilter.album) params.append('album', currentFilter.album);
  if (currentFilter.year) params.append('year', currentFilter.year);
  if (currentFilter.search) params.append('search', currentFilter.search);

  try {
    const res = await fetch(`/api/photos?${params.toString()}`);
    const data = await res.json();
    
    if (data.items.length === 0 && currentOffset === 0) {
      emptyState.classList.remove('hidden');
    } else {
      emptyState.classList.add('hidden');
    }

    if (data.items.length < PAGE_SIZE) {
      hasMore = false;
    }

    data.items.forEach((item) => {
      const globalIndex = currentPhotos.length;
      currentPhotos.push(item);
      renderGridItem(item, globalIndex);
    });

    currentOffset += data.items.length;
  } catch (err) {
    console.error('Error loading photos:', err);
  } finally {
    isLoading = false;
    loadingIndicator.classList.add('hidden');
  }
}

function resetAndLoadPhotos() {
  photosGrid.innerHTML = '';
  currentPhotos = [];
  currentOffset = 0;
  hasMore = true;
  loadPhotos();
}

// Render individual grid item
function renderGridItem(item, index) {
  const div = document.createElement('div');
  div.className = 'grid-item relative aspect-square bg-surface-subtle rounded overflow-hidden cursor-pointer group border border-surface-border/50';

  const isVideo = item.mime_type && item.mime_type.startsWith('video');

  div.innerHTML = `
    <img 
      src="/api/photos/${item.id}/thumb" 
      loading="lazy" 
      class="grid-img w-full h-full object-cover" 
      alt="${item.filename}"
      onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 24 24%22 fill=%22none%22 stroke=%22%23444%22 stroke-width=%221%22><rect width=%22100%25%22 height=%22100%25%22 fill=%22%2318181c%22/></svg>'"
    >
    ${isVideo ? `
      <div class="absolute bottom-1.5 left-1.5 px-1.5 py-0.5 rounded bg-black/70 text-[10px] text-white flex items-center space-x-1">
        <svg class="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><path d="M4 4l12 6-12 6V4z"/></svg>
        <span>VIDEO</span>
      </div>
    ` : ''}
    <div class="absolute inset-0 bg-black/20 opacity-0 group-hover:opacity-100 transition-opacity"></div>
  `;

  div.onclick = () => openLightbox(index);
  photosGrid.appendChild(div);
}

// Lightbox Handlers
function openLightbox(index) {
  if (index < 0 || index >= currentPhotos.length) return;
  currentIndex = index;
  const item = currentPhotos[index];

  const isVideo = item.mime_type && item.mime_type.startsWith('video');
  const fullUrl = `/api/photos/${item.id}/full`;

  if (isVideo) {
    lightboxImg.classList.add('hidden');
    lightboxVideo.classList.remove('hidden');
    lightboxVideo.src = fullUrl;
  } else {
    lightboxVideo.classList.add('hidden');
    lightboxVideo.pause();
    lightboxImg.classList.remove('hidden');
    // Pre-populate with cached thumbnail instantly while full loads
    lightboxImg.src = `/api/photos/${item.id}/thumb`;
    // Then stream full resolution
    const fullImg = new Image();
    fullImg.onload = () => {
      if (currentIndex === index) {
        lightboxImg.src = fullUrl;
      }
    };
    fullImg.src = fullUrl;
  }

  // Populate metadata
  metaFilename.textContent = item.filename || 'Unknown';
  metaDate.textContent = item.exif_timestamp ? new Date(item.exif_timestamp).toLocaleString() : (item.uploaded_at || 'Unknown');
  metaAlbum.textContent = item.album || 'Unassigned';
  metaSize.textContent = formatBytes(item.size_bytes);
  metaSha256.textContent = item.sha256 || '-';
  metaMsgId.textContent = `#${item.telegram_message_id || '0'}`;
  btnDownload.href = fullUrl;
  btnDownload.download = item.filename;

  lightbox.classList.remove('hidden');
}

function closeLightbox() {
  lightbox.classList.add('hidden');
  lightboxVideo.pause();
  lightboxVideo.src = '';
  lightboxImg.src = '';
  currentIndex = -1;
}

btnPrev.onclick = (e) => {
  e.stopPropagation();
  if (currentIndex > 0) openLightbox(currentIndex - 1);
};

btnNext.onclick = (e) => {
  e.stopPropagation();
  if (currentIndex < currentPhotos.length - 1) openLightbox(currentIndex + 1);
};

lightboxClose.onclick = closeLightbox;

// Keyboard navigation
window.addEventListener('keydown', (e) => {
  if (lightbox.classList.contains('hidden')) return;
  if (e.key === 'Escape') closeLightbox();
  if (e.key === 'ArrowLeft' && currentIndex > 0) openLightbox(currentIndex - 1);
  if (e.key === 'ArrowRight' && currentIndex < currentPhotos.length - 1) openLightbox(currentIndex + 1);
});

// Infinite Scroll
const scrollContainer = document.getElementById('main-scroll-container');
scrollContainer.addEventListener('scroll', () => {
  if (scrollContainer.scrollTop + scrollContainer.clientHeight >= scrollContainer.scrollHeight - 300) {
    loadPhotos();
  }
});

// Search debounce
let searchTimer = null;
searchInput.addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    currentFilter.search = e.target.value.trim() || null;
    resetAndLoadPhotos();
  }, 350);
});

// Filter reset button
document.querySelector('[data-filter="all"]').onclick = () => {
  currentFilter = { album: null, year: null, search: null };
  searchInput.value = '';
  resetAndLoadPhotos();
};

// Albums View Switcher
const navPhotos = document.getElementById('nav-photos');
const navAlbums = document.getElementById('nav-albums');

navPhotos.onclick = () => {
  navPhotos.className = 'px-3 py-1.5 rounded-md bg-neutral-800 text-neutral-100 transition-colors';
  navAlbums.className = 'px-3 py-1.5 rounded-md text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50 transition-colors';
  photosSection.classList.remove('hidden');
  albumsSection.classList.add('hidden');
};

navAlbums.onclick = async () => {
  navAlbums.className = 'px-3 py-1.5 rounded-md bg-neutral-800 text-neutral-100 transition-colors';
  navPhotos.className = 'px-3 py-1.5 rounded-md text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50 transition-colors';
  photosSection.classList.add('hidden');
  albumsSection.classList.remove('hidden');

  // Load albums
  albumsGrid.innerHTML = '<div class="text-xs text-neutral-500 py-6">Loading albums...</div>';
  try {
    const res = await fetch('/api/albums');
    const albums = await res.json();
    albumsGrid.innerHTML = '';
    albums.forEach(album => {
      const card = document.createElement('div');
      card.className = 'bg-surface-subtle border border-surface-border rounded-lg overflow-hidden cursor-pointer hover:border-neutral-600 transition-colors';
      card.innerHTML = `
        <div class="aspect-[4/3] bg-surface relative overflow-hidden">
          <img src="/api/photos/${album.cover_item_id}/thumb" class="w-full h-full object-cover">
        </div>
        <div class="p-3">
          <div class="font-medium text-sm text-neutral-200 truncate">${album.album}</div>
          <div class="text-xs text-neutral-500 font-mono mt-0.5">${album.count} items</div>
        </div>
      `;
      card.onclick = () => {
        currentFilter.album = album.album;
        navPhotos.click();
        resetAndLoadPhotos();
      };
      albumsGrid.appendChild(card);
    });
  } catch (err) {
    albumsGrid.innerHTML = '<div class="text-xs text-red-400 py-6">Failed to load albums</div>';
  }
};

// Initial Boot
initSidebarAndFilters();
loadPhotos();
