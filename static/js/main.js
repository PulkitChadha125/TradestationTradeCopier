// Trade Copier Control
document.addEventListener('DOMContentLoaded', function() {
    const startBtn = document.getElementById('start-copier-btn');
    const stopBtn = document.getElementById('stop-copier-btn');
    const statusText = document.getElementById('status-text');
    
    // Check copier status on load
    checkCopierStatus();
    
    // Check status every 5 seconds
    setInterval(checkCopierStatus, 5000);
    
    if (startBtn) {
        startBtn.addEventListener('click', function() {
            startCopier();
        });
    }
    
    if (stopBtn) {
        stopBtn.addEventListener('click', function() {
            stopCopier();
        });
    }
});

function checkCopierStatus() {
    fetch('/api/copier-status')
        .then(response => response.json())
        .then(data => {
            const startBtn = document.getElementById('start-copier-btn');
            const stopBtn = document.getElementById('stop-copier-btn');
            const statusText = document.getElementById('status-text');
            
            if (data.running) {
                statusText.textContent = 'Copier: Running';
                statusText.style.color = '#28a745';
                if (startBtn) startBtn.style.display = 'none';
                if (stopBtn) stopBtn.style.display = 'inline-block';
            } else {
                statusText.textContent = 'Copier: Stopped';
                statusText.style.color = '#666';
                if (startBtn) startBtn.style.display = 'inline-block';
                if (stopBtn) stopBtn.style.display = 'none';
            }
        })
        .catch(error => {
            console.error('Error checking copier status:', error);
        });
}

function startCopier() {
    fetch('/api/start-copier', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Trade copier started successfully!');
            checkCopierStatus();
        } else {
            alert('Error starting copier: ' + data.message);
        }
    })
    .catch(error => {
        console.error('Error starting copier:', error);
        alert('Error starting copier: ' + error.message);
    });
}

function stopCopier() {
    if (confirm('Are you sure you want to stop the trade copier?')) {
        fetch('/api/stop-copier', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                alert('Trade copier stopped.');
                checkCopierStatus();
            } else {
                alert('Error stopping copier: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error stopping copier:', error);
            alert('Error stopping copier: ' + error.message);
        });
    }
}
