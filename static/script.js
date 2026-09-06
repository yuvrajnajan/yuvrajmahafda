/**
 * Maha FDA – Citizen Complaint Portal
 * Interactive Client Scripts
 */

document.addEventListener('DOMContentLoaded', function () {
  // 1. Mobile Navigation Toggle
  const hamburgerBtn = document.getElementById('hamburgerBtn');
  const mainNav = document.getElementById('mainNav');

  if (hamburgerBtn && mainNav) {
    hamburgerBtn.addEventListener('click', function () {
      mainNav.classList.toggle('show');
    });
  }

  // 2. Setup Complaint Form Handlers if on Complaint page
  const photoInput = document.getElementById('photoInput');
  if (photoInput) {
    photoInput.addEventListener('change', handlePhotoSelection);
  }

  const complaintForm = document.getElementById('complaintForm');
  if (complaintForm) {
    complaintForm.addEventListener('submit', handleComplaintSubmit);
  }
});

// Photo Upload Validation and Live Preview
function handlePhotoSelection(e) {
  const file = e.target.files[0];
  const previewContainer = document.getElementById('photoPreviewContainer');
  const previewImg = document.getElementById('photoPreviewImg');

  if (!file) {
    if (previewContainer) previewContainer.style.display = 'none';
    return;
  }

  // Allowed formats: JPG, JPEG, PNG, WEBP
  const allowedExtensions = ['jpg', 'jpeg', 'png', 'webp'];
  const ext = file.name.split('.').pop().toLowerCase();

  if (!allowedExtensions.includes(ext)) {
    alert('Invalid image format. Please select a JPG, JPEG, PNG, or WEBP photo.');
    e.target.value = '';
    if (previewContainer) previewContainer.style.display = 'none';
    return;
  }

  // Max 5 MB validation (5 * 1024 * 1024 bytes)
  const maxSizeInBytes = 5 * 1024 * 1024;
  if (file.size > maxSizeInBytes) {
    alert('File size exceeds the 5 MB limit. Please select a smaller photo.');
    e.target.value = '';
    if (previewContainer) previewContainer.style.display = 'none';
    return;
  }

  // Show live preview
  const reader = new FileReader();
  reader.onload = function (event) {
    if (previewImg && previewContainer) {
      previewImg.src = event.target.result;
      previewContainer.style.display = 'block';
    }
  };
  reader.readAsDataURL(file);
}

// Browser Geolocation API Handler
function detectLocation() {
  const statusEl = document.getElementById('location-status');
  const locationInput = document.getElementById('locationInput');
  const latInput = document.getElementById('latitudeInput');
  const lonInput = document.getElementById('longitudeInput');
  const mapBtn = document.getElementById('googleMapsBtn');

  if (!navigator.geolocation) {
    if (statusEl) {
      statusEl.textContent = '❌ Geolocation is not supported by your browser. Please type the location.';
      statusEl.style.color = '#DC2626';
    }
    return;
  }

  if (statusEl) {
    statusEl.textContent = '📍 Acquiring precise GPS coordinates...';
    statusEl.style.color = '#0879BD';
  }

  const geoOptions = {
    enableHighAccuracy: true,
    timeout: 15000,
    maximumAge: 0
  };

  navigator.geolocation.getCurrentPosition(
    function (position) {
      const lat = position.coords.latitude.toFixed(6);
      const lon = position.coords.longitude.toFixed(6);

      if (latInput) latInput.value = lat;
      if (lonInput) lonInput.value = lon;

      if (locationInput) {
        if (!locationInput.value.trim() || locationInput.value.includes('GPS:')) {
          locationInput.value = `GPS: ${lat}, ${lon}`;
        }
      }

      if (mapBtn) {
        mapBtn.href = `https://www.google.com/maps?q=${lat},${lon}`;
        mapBtn.style.display = 'inline-flex';
      }

      if (statusEl) {
        statusEl.textContent = '✅ Location detected successfully.';
        statusEl.style.color = '#159B91';
      }
    },
    function (error) {
      if (statusEl) {
        if (error.code === error.PERMISSION_DENIED) {
          statusEl.textContent = '❌ Location permission denied. Please enter the address manually.';
        } else if (error.code === error.TIMEOUT) {
          statusEl.textContent = '⚠️ Location detection timed out. Please enter the address manually.';
        } else {
          statusEl.textContent = '⚠️ Unable to retrieve GPS coordinates. Please enter the address manually.';
        }
        statusEl.style.color = '#EA580C';
      }
    },
    geoOptions
  );
}

// Client-side Form Validation
function handleComplaintSubmit(e) {
  const mobileInput = document.getElementById('mobileInput');
  if (mobileInput) {
    const mobileVal = mobileInput.value.trim();
    const mobileRegex = /^[0-9]{10}$/;
    if (!mobileRegex.test(mobileVal)) {
      alert('Please enter a valid 10-digit mobile number without spaces or country code.');
      mobileInput.focus();
      e.preventDefault();
      return false;
    }
  }

  const submitBtn = document.getElementById('submitBtn');
  if (submitBtn) {
    submitBtn.innerHTML = '⏳ Submitting Complaint & Analyzing Risk... Please wait';
    submitBtn.disabled = true;
    submitBtn.style.opacity = '0.8';
  }
}
